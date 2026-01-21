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

import argparse
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
        - matrix: minimal required fields for Matrix (homeserver, bot_user_id, and either access_token or password)
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

    @patch("mmrelay.cli._validate_credentials_json")
    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("mmrelay.cli._print_unified_e2ee_analysis")
    @patch("mmrelay.e2ee_utils.get_e2ee_status")
    @patch("builtins.print")
    def test_check_config_missing_matrix_section_with_credentials(
        self,
        mock_print,
        mock_get_e2ee_status,
        mock_print_unified_e2ee,
        mock_validate_yaml,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
        mock_validate_credentials,
    ):
        """
        Test that check_config succeeds when the `matrix` section is absent but a valid credentials.json is present.

        Simulates a configuration file missing the entire `matrix` section while providing required
        room and meshtastic settings. Mocks:
        - argument parsing to use default config discovery,
        - config path discovery and file existence,
        - YAML validation to return the config without a `matrix` section,
        - credentials validation to report a valid credentials.json,
        - unified E2EE status retrieval to report a ready state.

        Asserts that check_config returns True, credentials validation is invoked, and the unified
        E2EE status is queried.
        """
        args = MagicMock()
        args.config = None
        mock_parse_args.return_value = args

        # Config with NO matrix section - credentials.json provides all auth info
        config_without_matrix = {
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {
                "connection_type": "tcp",
                "host": "localhost",
                "broadcast_enabled": True,
            },
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, config_without_matrix)
        # Mock the unified E2EE status to return a ready state
        mock_get_e2ee_status.return_value = {
            "overall_status": "ready",
            "enabled": True,
            "available": True,
            "configured": True,
            "issues": [],
        }
        mock_validate_credentials.return_value = True  # Valid credentials.json exists

        with patch("builtins.open", mock_open(read_data="test")):
            result = check_config()

        self.assertTrue(result)
        mock_print.assert_any_call("\n✅ Configuration file is valid!")
        mock_validate_credentials.assert_called_once()
        mock_get_e2ee_status.assert_called_once()

    @patch("mmrelay.config.os.makedirs")
    def test_get_config_paths(self, mock_makedirs):
        """
        Verify get_config_paths() returns a list of candidate configuration file paths.

        Asserts that the result is a list with at least three entries and that each returned path ends with "config.yaml".
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
    @patch("mmrelay.cli._print_unified_e2ee_analysis")
    @patch("mmrelay.e2ee_utils.get_e2ee_status")
    @patch("builtins.print")
    @patch("builtins.open", new_callable=mock_open)
    def test_check_config_valid_tcp(
        self,
        mock_open,
        mock_print,
        mock_get_e2ee_status,
        mock_print_unified_e2ee,
        mock_validate_yaml,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """
        Test that `check_config` returns True and prints success messages when provided with a valid TCP configuration.
        """
        mock_parse_args.return_value = self.mock_args
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, self.valid_config)
        # Mock the unified E2EE status to return a ready state
        mock_get_e2ee_status.return_value = {
            "overall_status": "ready",
            "enabled": True,
            "available": True,
            "configured": True,
            "issues": [],
        }

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
        self,
        mock_open,
        mock_print,
        mock_validate_e2ee,
        mock_validate_yaml,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
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
    @patch("mmrelay.cli._print_unified_e2ee_analysis")
    @patch("mmrelay.e2ee_utils.get_e2ee_status")
    @patch("builtins.print")
    @patch("builtins.open", new_callable=mock_open)
    def test_check_config_valid_ble(
        self,
        mock_open,
        mock_print,
        mock_get_e2ee_status,
        mock_print_unified_e2ee,
        mock_validate_yaml,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
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
        # Mock the unified E2EE status to return a ready state
        mock_get_e2ee_status.return_value = {
            "overall_status": "ready",
            "enabled": True,
            "available": True,
            "configured": True,
            "issues": [],
        }

        with patch("mmrelay.cli._validate_credentials_json", return_value=False):
            result = check_config()

        self.assertTrue(result)
        mock_print.assert_any_call("\n✅ Configuration file is valid!")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.print")
    def test_check_config_no_file_found(
        self, mock_print, mock_isfile, mock_get_paths, mock_parse_args
    ):
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
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
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
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
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
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """
        Test that check_config fails when required fields are missing from the 'matrix' section.
        """
        mock_parse_args.return_value = self.mock_args
        invalid_config = {
            "matrix": {"homeserver": "https://matrix.org"},
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
            "Error: Missing authentication in 'matrix' section: provide 'access_token' or 'password'"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("mmrelay.cli._print_unified_e2ee_analysis")
    @patch("mmrelay.e2ee_utils.get_e2ee_status")
    @patch("builtins.print")
    def test_check_config_valid_password_auth(
        self,
        mock_print,
        mock_get_e2ee_status,
        mock_print_unified_e2ee,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """Test check_config with valid password-based authentication."""
        mock_parse_args.return_value = argparse.Namespace(config=None)

        valid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "bot_user_id": "@bot:matrix.org",
                "password": "secret123",
            },
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "tcp", "host": "localhost"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, valid_config)

        mock_get_e2ee_status.return_value = {
            "overall_status": "ready",
            "enabled": True,
            "available": True,
            "configured": True,
            "issues": [],
        }
        with patch("mmrelay.cli._validate_credentials_json", return_value=False):
            result = check_config()

        self.assertTrue(result)
        mock_print.assert_any_call("\n✅ Configuration file is valid!")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_invalid_password_missing_homeserver(
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """Test check_config with password but missing homeserver."""
        mock_parse_args.return_value = argparse.Namespace(config=None)

        invalid_config = {
            "matrix": {
                "bot_user_id": "@bot:matrix.org",
                "password": "secret123",
                # missing homeserver
            },
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "tcp", "host": "localhost"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_credentials_json", return_value=False):
            result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing required fields in 'matrix' section: homeserver"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_invalid_password_missing_bot_user_id(
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """Test check_config with password but missing bot_user_id."""
        mock_parse_args.return_value = argparse.Namespace(config=None)
        invalid_config = {
            "matrix": {"homeserver": "https://matrix.org", "password": "secret123"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "tcp", "host": "localhost"},
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)
        with patch("mmrelay.cli._validate_credentials_json", return_value=False):
            result = check_config()
        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing required fields in 'matrix' section: bot_user_id"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_no_auth_methods(
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """Test check_config with neither access_token nor password."""
        mock_parse_args.return_value = argparse.Namespace(config=None)

        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "bot_user_id": "@bot:matrix.org",
                # no access_token or password
            },
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "tcp", "host": "localhost"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_credentials_json", return_value=False):
            result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call(
            "Error: Missing authentication in 'matrix' section: provide 'access_token' or 'password'"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("mmrelay.cli._print_unified_e2ee_analysis")
    @patch("mmrelay.e2ee_utils.get_e2ee_status")
    @patch("builtins.print")
    def test_check_config_empty_password(
        self,
        mock_print,
        mock_get_e2ee_status,
        mock_print_unified_e2ee,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """Test check_config with empty string password."""
        mock_parse_args.return_value = argparse.Namespace(config=None)

        invalid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "bot_user_id": "@bot:matrix.org",
                "password": "",  # empty password
            },
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "tcp", "host": "localhost"},
        }

        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)

        mock_get_e2ee_status.return_value = {
            "overall_status": "ready",
            "enabled": True,
            "available": True,
            "configured": True,
            "issues": [],
        }
        with patch("mmrelay.cli._validate_credentials_json", return_value=False):
            result = check_config()

        self.assertTrue(result)
        mock_print.assert_any_call("\n✅ Configuration file is valid!")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_missing_matrix_rooms(
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
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
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
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
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
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
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
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
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
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
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, invalid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertFalse(result)
        mock_print.assert_any_call("Error: Missing 'meshtastic' section in config")
        mock_print.assert_any_call(
            "   You need to configure Meshtastic connection settings."
        )
        mock_print.assert_any_call("   Example:")
        mock_print.assert_any_call("     meshtastic:")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_missing_connection_type(
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
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
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
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
        mock_print.assert_any_call("   Add connection_type: 'tcp', 'serial', or 'ble'")

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_invalid_connection_type(
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
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
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
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
            "Error: Invalid 'connection_type': invalid_type. Must be 'tcp', 'serial', 'ble' or 'network' (deprecated)"
        )

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_missing_serial_port(
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
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
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
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
    @patch("mmrelay.cli._print_unified_e2ee_analysis")
    @patch("mmrelay.e2ee_utils.get_e2ee_status")
    @patch("builtins.print")
    def test_check_config_valid_serial_port_linux(
        self,
        mock_print,
        mock_get_e2ee_status,
        _mock_print_unified_e2ee,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """
        Test that check_config succeeds with valid Linux serial port format.
        """
        mock_parse_args.return_value = self.mock_args
        valid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"},
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, valid_config)
        mock_get_e2ee_status.return_value = {
            "overall_status": "ready",
            "enabled": True,
            "available": True,
            "configured": True,
            "issues": [],
        }

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertTrue(result)

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("mmrelay.cli._print_unified_e2ee_analysis")
    @patch("mmrelay.e2ee_utils.get_e2ee_status")
    @patch("builtins.print")
    def test_check_config_valid_serial_port_windows(
        self,
        mock_print,
        mock_get_e2ee_status,
        _mock_print_unified_e2ee,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """
        Test that check_config succeeds with valid Windows serial port format.
        """
        mock_parse_args.return_value = self.mock_args
        valid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "serial", "serial_port": "COM3"},
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, valid_config)
        mock_get_e2ee_status.return_value = {
            "overall_status": "ready",
            "enabled": True,
            "available": True,
            "configured": True,
            "issues": [],
        }

        # Mock platform as Windows for this test
        with patch("platform.system", return_value="Windows"):
            with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
                with patch(
                    "mmrelay.cli._validate_credentials_json", return_value=False
                ):
                    result = check_config()

        self.assertTrue(result)

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_valid_host_ipv4(
        self,
        _mock_print,
        mock_validate_yaml,
        _mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """
        Test that check_config succeeds with valid IPv4 host.
        """
        mock_parse_args.return_value = self.mock_args
        valid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "tcp", "host": "192.168.1.1"},
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, valid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertTrue(result)

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_valid_host_ipv6(
        self,
        _mock_print,
        mock_validate_yaml,
        _mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """
        Test that check_config succeeds with valid IPv6 host.
        """
        mock_parse_args.return_value = self.mock_args
        valid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "tcp", "host": "2001:db8::1"},
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, valid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertTrue(result)

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_valid_host_hostname(
        self,
        mock_print,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """
        Test that check_config succeeds with valid hostname.
        """
        mock_parse_args.return_value = self.mock_args
        valid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "tcp", "host": "meshtastic.local"},
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, valid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertTrue(result)

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_valid_ble_address_mac(
        self,
        _mock_print,
        mock_validate_yaml,
        _mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """
        Test that check_config succeeds with valid BLE MAC address.
        """
        mock_parse_args.return_value = self.mock_args
        valid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {
                "connection_type": "ble",
                "ble_address": "AA:BB:CC:DD:EE:FF",
            },
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, valid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertTrue(result)

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.get_config_paths")
    @patch("os.path.isfile")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.cli.validate_yaml_syntax")
    @patch("builtins.print")
    def test_check_config_valid_ble_address_device_name(
        self,
        _mock_print,
        mock_validate_yaml,
        _mock_open,
        mock_isfile,
        mock_get_paths,
        mock_parse_args,
    ):
        """
        Test that check_config succeeds with valid BLE device name.
        """
        mock_parse_args.return_value = self.mock_args
        valid_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {
                "connection_type": "ble",
                "ble_address": "MyMeshtasticDevice",
            },
        }
        mock_get_paths.return_value = ["/test/config.yaml"]
        mock_isfile.return_value = True
        mock_validate_yaml.return_value = (True, None, valid_config)

        with patch("mmrelay.cli._validate_e2ee_config", return_value=True):
            with patch("mmrelay.cli._validate_credentials_json", return_value=False):
                result = check_config()

        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
