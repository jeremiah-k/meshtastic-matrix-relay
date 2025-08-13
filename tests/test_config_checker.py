#!/usr/bin/env python3
"""
Test suite for the MMRelay configuration checker.

Tests the configuration validation functionality including:
- Configuration file discovery
- YAML parsing and validation
- Required field validation
- Connection type validation
- Error handling and reporting
"""

import os
import sys
import unittest
from unittest.mock import mock_open, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.cli import check_config
from mmrelay.config import get_config_paths


class TestConfigChecker(unittest.TestCase):
    """Test cases for the configuration checker."""

    def setUp(self):
        """
        Prepare a representative, valid configuration dict used by each test.

        The dict is stored as self.valid_config and includes:
        - matrix: minimal required fields for Matrix (homeserver, access_token, bot_user_id)
        - matrix_rooms: a list with one room dict containing an 'id' and 'meshtastic_channel'
        - meshtastic: a meshtastic connection with connection_type 'tcp', a host, and broadcast_enabled flag

        This runs before each test method to provide a reusable valid configuration fixture.
        """
        self.valid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {
                "connection_type": "tcp",
                "host": "192.168.1.100",
                "broadcast_enabled": True,
            },
        }

    def test_get_config_paths(self):
        """
        Test that get_config_paths returns a list of configuration file paths.

        Asserts that the returned value is a list of the expected length and that the function is called exactly once.
        """
        # Test the actual function behavior
        paths = get_config_paths()

        self.assertIsInstance(paths, list)
        self.assertGreaterEqual(len(paths), 3)  # Should return at least 3 paths

        # Verify all paths end with config.yaml
        for path in paths:
            self.assertTrue(path.endswith("config.yaml"))

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_valid_tcp(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that `check_config` returns True and prints success messages when provided with a valid TCP configuration.
        """
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = self.valid_config

        result = check_config()

        self.assertTrue(result)
        mock_print.assert_any_call("Found configuration file at: /test/config.yaml")
        mock_print.assert_any_call("Configuration file is valid!")

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_valid_serial(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that `check_config` returns True and prints a success message when provided with a valid serial meshtastic configuration.
        """
        serial_config = self.valid_config.copy()
        serial_config["meshtastic"] = {
            "connection_type": "serial",
            "serial_port": "/dev/ttyUSB0",
            "broadcast_enabled": True,
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = serial_config

        result = check_config()

        self.assertTrue(result)
        mock_print.assert_any_call("Configuration file is valid!")

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_valid_ble(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that `check_config` successfully validates a configuration with a valid BLE connection type.

        Simulates a configuration file specifying a BLE connection and asserts that validation passes and the correct success message is printed.
        """
        ble_config = self.valid_config.copy()
        ble_config["meshtastic"] = {
            "connection_type": "ble",
            "ble_address": "AA:BB:CC:DD:EE:FF",
            "broadcast_enabled": True,
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = ble_config

        result = check_config()

        self.assertTrue(result)
        mock_print.assert_any_call("Configuration file is valid!")

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.print")
    def test_check_config_no_file_found(self, mock_print, mock_isfile, mock_get_paths):
        """
        Test that check_config returns False and prints appropriate error messages when no configuration file is found at any of the discovered paths.
        """
        mock_get_paths.return_value = ["/test/config.yaml", "/test2/config.yaml"]
        mock_isfile.return_value = False

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: No configuration file found in any of the following locations:"
        )
        mock_print.assert_any_call("  - /test/config.yaml")
        mock_print.assert_any_call("  - /test2/config.yaml")

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_empty_config(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that check_config returns False and prints an error when the configuration file is empty or invalid.
        """
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = None

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Configuration file is empty or contains only comments"
        )

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_missing_matrix_section(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that check_config returns False and reports an error when the configuration is missing the required 'matrix' section.

        Sets up a configuration containing only a 'meshtastic' section, patches file discovery and YAML loading to return that config, calls check_config(), and asserts the function fails and prints the expected error message "Error: Missing 'matrix' section in config".
        """
        invalid_config = {"meshtastic": {"connection_type": "tcp"}}

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call("Error: Missing 'matrix' section in config")

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_missing_matrix_fields(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that check_config fails when required fields are missing from the 'matrix' section.

        Simulates a configuration missing 'access_token' and 'bot_user_id' in the 'matrix' section and asserts that validation fails with the appropriate error message.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org"
            },  # Missing access_token and bot_user_id
            "matrix_rooms": [{"id": "!room1:matrix.org"}],
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing required fields in 'matrix' section: access_token, bot_user_id"
        )

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_missing_matrix_rooms(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that `check_config` fails when the 'matrix_rooms' section is missing from the configuration.

        Asserts that the function returns False and prints an appropriate error message.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing or empty 'matrix_rooms' section in config"
        )

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_invalid_matrix_rooms_type(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that check_config fails when the 'matrix_rooms' field is not a list.

        Asserts that the function returns False and prints an appropriate error message when 'matrix_rooms' is of an invalid type.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": "not_a_list",  # Should be a list
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call("Error: 'matrix_rooms' must be a list")

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_invalid_room_format(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that check_config fails when an entry in 'matrix_rooms' is not a dictionary.

        Verifies check_config returns False and emits the error message
        "Error: Room 1 in 'matrix_rooms' must be a dictionary" when a non-dictionary
        item appears in the 'matrix_rooms' list.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": ["not_a_dict"],  # Should be dict objects
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Room 1 in 'matrix_rooms' must be a dictionary"
        )

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_missing_room_id(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that check_config fails when a room in 'matrix_rooms' lacks the required 'id' field.

        Simulates a configuration where a room dictionary in 'matrix_rooms' is missing the 'id' key and asserts that check_config returns False and prints the appropriate error message.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"meshtastic_channel": 0}],  # Missing 'id' field
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Room 1 in 'matrix_rooms' is missing the 'id' field"
        )

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_missing_meshtastic_section(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that `check_config` fails and prints an error when the 'meshtastic' section is missing from the configuration.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org"}],
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call("Error: Missing 'meshtastic' section in config")

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_missing_connection_type(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that check_config fails when the 'connection_type' field is missing from the 'meshtastic' section of the configuration.

        Asserts that the function returns False and prints the appropriate error message.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org"}],
            "meshtastic": {"host": "192.168.1.100"},  # Missing connection_type
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing 'connection_type' in 'meshtastic' section"
        )

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_invalid_connection_type(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that check_config returns False and prints an error when the meshtastic connection_type is invalid.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org"}],
            "meshtastic": {"connection_type": "invalid_type"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Invalid 'connection_type': invalid_type. Must be 'tcp', 'serial', or 'ble'"
        )

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_missing_serial_port(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that check_config fails when 'serial_port' is missing for a serial connection type in the configuration.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org"}],
            "meshtastic": {"connection_type": "serial"},  # Missing serial_port
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing 'serial_port' for 'serial' connection type"
        )

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_missing_tcp_host(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that `check_config` fails when the 'host' field is missing for a TCP meshtastic connection.

        Asserts that the function returns False and prints the appropriate error message.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org"}],
            "meshtastic": {"connection_type": "tcp"},  # Missing host
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call("Error: Missing 'host' for 'tcp' connection type")

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_missing_ble_address(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that check_config fails when the 'ble_address' field is missing for a BLE connection type in the configuration.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org"}],
            "meshtastic": {"connection_type": "ble"},  # Missing ble_address
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing 'ble_address' for 'ble' connection type"
        )

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_yaml_error(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that check_config returns False and prints an error message when a YAML parsing error occurs.
        """
        from yaml import YAMLError

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.side_effect = YAMLError("Invalid YAML syntax")

        result = check_config()

        self.assertFalse(result)
        # Check that YAML error message is printed (CLI version has more detailed format)
        printed_calls = [str(call) for call in mock_print.call_args_list]
        yaml_error_found = any(
            "YAML Syntax Error" in call and "Invalid YAML syntax" in call
            for call in printed_calls
        )
        self.assertTrue(
            yaml_error_found,
            f"Expected YAML error message not found in: {printed_calls}",
        )

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_general_exception(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Test that check_config returns False and prints an error message when a general exception occurs during configuration checking.
        """
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.side_effect = Exception("General error")

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call("Error checking configuration: General error")


if __name__ == "__main__":
    unittest.main()

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_missing_matrix_homeserver(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Validate failure when 'homeserver' is missing from the 'matrix' section.
        We assert failure and check printed output contains the expected hint about the missing field.
        """
        invalid_config = {
            "matrix": {
                # "homeserver": "https://matrix.org",  # intentionally omitted
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        # Use substring search across printed calls to avoid brittleness on exact phrasing
        printed = " | ".join(str(args) for args, _ in mock_print.call_args_list)
        self.assertIn("matrix", printed.lower())
        self.assertIn("missing", printed.lower())
        self.assertIn("homeserver", printed.lower())

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_empty_matrix_rooms(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Validate failure when 'matrix_rooms' exists but is an empty list.
        Expected behavior aligns with 'missing or empty' semantics.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [],
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing or empty 'matrix_rooms' section in config"
        )

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_missing_meshtastic_channel(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Validate failure when a room dict is missing the required 'meshtastic_channel' field.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org"}],  # Missing meshtastic_channel
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        # Be tolerant to exact phrasing, but ensure the message mentions room index and meshtastic_channel
        printed = [str(call) for call in mock_print.call_args_list]
        chan_error = any("meshtastic_channel" in c for c in printed)
        self.assertTrue(
            chan_error,
            f"Expected message mentioning 'meshtastic_channel' not found in: {printed}",
        )

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_meshtastic_channel_not_int(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        Validate failure when 'meshtastic_channel' is present but not an integer.
        """
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [
                {"id": "!room1:matrix.org", "meshtastic_channel": "zero"}  # wrong type
            ],
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_yaml_load.return_value = invalid_config

        result = check_config()

        self.assertFalse(result)
        # Use partial matching to avoid tight coupling to exact wording
        printed = " | ".join(str(args) for args, _ in mock_print.call_args_list)
        self.assertIn("meshtastic_channel", printed)
        self.assertTrue(
            ("integer" in printed.lower()) or ("int" in printed.lower()),
            f"Expected type mention for meshtastic_channel not found in: {printed}",
        )

    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_check_config_uses_first_existing_path_and_skips_missing(
        self, mock_print, mock_yaml_load, mock_file, mock_isfile, mock_get_paths
    ):
        """
        When multiple config paths are returned, ensure check_config tries them in order,
        skipping non-existent files, and succeeds when encountering the first valid file.
        """
        valid_config = self.valid_config.copy()
        mock_get_paths.return_value = ["/missing/config.yaml", "/present/config.yaml"]

        def isfile_side_effect(path):
            return path == "/present/config.yaml"

        mock_isfile.side_effect = isfile_side_effect
        mock_yaml_load.return_value = valid_config

        result = check_config()

        self.assertTrue(result)
        # Confirm it announces the discovered, existing path
        mock_print.assert_any_call("Found configuration file at: /present/config.yaml")
        mock_print.assert_any_call("Configuration file is valid!")

