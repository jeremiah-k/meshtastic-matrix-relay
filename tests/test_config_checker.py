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
from unittest.mock import MagicMock, mock_open, patch

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
        # Common mock for args
        self.mock_args = MagicMock()
        self.mock_args.config = "/test/config.yaml"


    @patch("mmrelay.config.os.makedirs")
    def test_get_config_paths(self, mock_makedirs):
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

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("mmrelay.cli._validate_e2ee_config")
    @patch("builtins.print")
    @patch("builtins.open", new_callable=mock_open)
    def test_check_config_valid_tcp(
        self, mock_open, mock_print, mock_validate_e2ee, mock_validate_yaml, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that `check_config` returns True and prints success messages when provided with a valid TCP configuration.
        """
        mock_parse_args.return_value = self.mock_args
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, self.valid_config)
        mock_validate_e2ee.return_value = True

        with patch("mmrelay.cli._validate_credentials_json", return_value=False):
            result = check_config()

        self.assertTrue(result)
        mock_print.assert_any_call("Found configuration file at: /test/config.yaml")
        mock_print.assert_any_call("\n✅ Configuration file is valid!")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("mmrelay.cli._validate_e2ee_config")
    @patch("builtins.print")
    @patch("builtins.open", new_callable=mock_open)
    def test_check_config_valid_serial(
        self, mock_open, mock_print, mock_validate_e2ee, mock_validate_yaml, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that `check_config` returns True and prints a success message when provided with a valid serial meshtastic configuration.
        """
        mock_parse_args.return_value = self.mock_args
        serial_config = self.valid_config.copy()
        serial_config["meshtastic"] = {
            "connection_type": "serial",
            "serial_port": "/dev/ttyUSB0",
            "broadcast_enabled": True,
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, serial_config)
        mock_validate_e2ee.return_value = True

        with patch("mmrelay.cli._validate_credentials_json", return_value=False):
            result = check_config()

        self.assertTrue(result)
        mock_print.assert_any_call("\n✅ Configuration file is valid!")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("mmrelay.cli._validate_e2ee_config")
    @patch("builtins.print")
    @patch("builtins.open", new_callable=mock_open)
    def test_check_config_valid_ble(
        self, mock_open, mock_print, mock_validate_e2ee, mock_validate_yaml, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that `check_config` successfully validates a configuration with a valid BLE connection type.

        Simulates a configuration file specifying a BLE connection and asserts that validation passes and the correct success message is printed.
        """
        mock_parse_args.return_value = self.mock_args
        ble_config = self.valid_config.copy()
        ble_config["meshtastic"] = {
            "connection_type": "ble",
            "ble_address": "AA:BB:CC:DD:EE:FF",
            "broadcast_enabled": True,
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, ble_config)
        mock_validate_e2ee.return_value = True

        with patch("mmrelay.cli._validate_credentials_json", return_value=False):
            result = check_config()

        self.assertTrue(result)
        mock_print.assert_any_call("\n✅ Configuration file is valid!")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.print")
    def test_check_config_no_file_found(self, mock_print, mock_isfile, mock_get_paths, mock_parse_args):
        """
        Test that check_config returns False and prints appropriate error messages when no configuration file is found at any of the discovered paths.
        """
        args = MagicMock()
        args.config = None
        mock_parse_args.return_value = args
        mock_get_paths.return_value = ["/test/config.yaml", "/test2/config.yaml"]
        mock_isfile.return_value = False

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: No configuration file found in any of the following locations:"
        )
        mock_print.assert_any_call("  - /test/config.yaml")
        mock_print.assert_any_call("  - /test2/config.yaml")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_empty_config(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that check_config returns False and prints an error when the configuration file is empty or invalid.
        """
        mock_parse_args.return_value = self.mock_args
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, None)

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Configuration file is empty or contains only comments"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_missing_matrix_section(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that check_config returns False and reports an error when the configuration is missing the required 'matrix' section.
        """
        mock_parse_args.return_value = self.mock_args
        invalid_config = {"meshtastic": {"connection_type": "tcp"}}
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call("Error: Missing 'matrix' section in config")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_missing_matrix_fields(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that check_config fails when required fields are missing from the 'matrix' section.
        """
        mock_parse_args.return_value = self.mock_args
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org"
            },
            "matrix_rooms": [{"id": "!room1:matrix.org"}],
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_credentials_json", return_value=False):
            result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing required fields in 'matrix' section: access_token, bot_user_id"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_missing_matrix_rooms(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that `check_config` fails when the 'matrix_rooms' section is missing from the configuration.
        """
        mock_parse_args.return_value = self.mock_args
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
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing or empty 'matrix_rooms' section in config"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_invalid_matrix_rooms_type(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that check_config fails when the 'matrix_rooms' field is not a list.
        """
        mock_parse_args.return_value = self.mock_args
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": "not_a_list",
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"},
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call("Error: 'matrix_rooms' must be a list")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_invalid_room_format(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that check_config fails when an entry in 'matrix_rooms' is not a dictionary.
        """
        mock_parse_args.return_value = self.mock_args
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": ["not_a_dict"],
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"},
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Room 1 in 'matrix_rooms' must be a dictionary"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_missing_room_id(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that check_config fails when a room in 'matrix_rooms' lacks the required 'id' field.
        """
        mock_parse_args.return_value = self.mock_args
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"},
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Room 1 in 'matrix_rooms' is missing the 'id' field"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_missing_meshtastic_section(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that `check_config` fails and prints an error when the 'meshtastic' section is missing.
        """
        mock_parse_args.return_value = self.mock_args
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
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call("Error: Missing 'meshtastic' section in config")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_missing_connection_type(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that check_config fails when 'connection_type' is missing from the 'meshtastic' section.
        """
        mock_parse_args.return_value = self.mock_args
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org"}],
            "meshtastic": {"host": "192.168.1.100"},
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing 'connection_type' in 'meshtastic' section"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_invalid_connection_type(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that check_config fails with an invalid meshtastic connection_type.
        """
        mock_parse_args.return_value = self.mock_args
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
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Invalid 'connection_type': invalid_type. Must be 'tcp', 'serial', or 'ble'"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_missing_serial_port(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that check_config fails when 'serial_port' is missing for a serial connection.
        """
        mock_parse_args.return_value = self.mock_args
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org"}],
            "meshtastic": {"connection_type": "serial"},
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing 'serial_port' for 'serial' connection type"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_missing_tcp_host(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that `check_config` fails when 'host' is missing for a TCP connection.
        """
        mock_parse_args.return_value = self.mock_args
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org"}],
            "meshtastic": {"connection_type": "tcp"},
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call("Error: Missing 'host' for 'tcp' connection type")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_missing_ble_address(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that check_config fails when 'ble_address' is missing for a BLE connection.
        """
        mock_parse_args.return_value = self.mock_args
        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org"}],
            "meshtastic": {"connection_type": "ble"},
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing 'ble_address' for 'ble' connection type"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_yaml_error(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that check_config returns False and prints an error message when a YAML parsing error occurs.
        """
        mock_parse_args.return_value = self.mock_args
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (False, "Invalid YAML syntax", None)

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call("YAML Syntax Error:\nInvalid YAML syntax")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_general_exception(
        self, mock_print, mock_validate_yaml, mock_open, mock_isfile, mock_get_paths, mock_parse_args
    ):
        """
        Test that check_config returns False and prints an error message for a general exception.
        """
        mock_parse_args.return_value = self.mock_args
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.side_effect = Exception("General error")

        result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call("Error checking configuration: General error")


if __name__ == "__main__":
    unittest.main()
