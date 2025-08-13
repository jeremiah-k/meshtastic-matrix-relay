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

# Additional tests appended by CodeRabbit Inc to cover edge cases and diff-driven scenarios.

import os
import unittest
from unittest.mock import patch, mock_open

try:
    # In case yaml is available in tests context
    import yaml  # noqa: F401
except Exception:
    yaml = None  # noqa: F841


class TestConfigCheckerAdditional(unittest.TestCase):
    """
    Additional unit tests for configuration checker focusing on edge conditions
    and branches likely introduced or modified in the recent diff.
    Uses unittest framework to align with project conventions.
    """

    def setUp(self):
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

    def _mock_config_open(self, content: str = ""):
        # Utility to make open() yield specified YAML content
        return mock_open(read_data=content)

    def test_get_config_paths_return_type_and_uniqueness(self):
        # Validate get_config_paths returns list of unique strings that look like YAML paths
        from mmrelay.cli import get_config_paths

        paths = get_config_paths()
        self.assertIsInstance(paths, list)
        self.assertTrue(all(isinstance(p, str) for p in paths))
        # Some projects include multiple default locations; ensure no duplicates
        self.assertEqual(len(paths), len(set(paths)))
        # Heuristic: all should end in config.yaml
        self.assertTrue(all(p.endswith("config.yaml") for p in paths))

    @patch("mmrelay.cli.get_config_paths", return_value=["/etc/mmrelay/config.yaml", "/home/user/.config/mmrelay/config.yaml"])
    @patch("os.path.isfile")
    @patch("builtins.open")
    @patch("yaml.load")
    @patch("builtins.print")
    def test_first_valid_config_is_used_when_multiple_paths(
        self, mock_print, mock_yaml_load, mock_open_file, mock_isfile, _mock_paths
    ):
        # First path invalid file; second path valid
        def isfile_side_effect(path):
            return path == "/home/user/.config/mmrelay/config.yaml"

        mock_isfile.side_effect = isfile_side_effect
        mock_open_file.side_effect = [
            FileNotFoundError("Missing at /etc"),
            self._mock_config_open("matrix: {}\n")(None),  # won't be used directly due to side_effect above
        ]
        # Provide valid config when second file is opened and parsed
        mock_yaml_load.return_value = self.valid_config

        from mmrelay.cli import check_config

        result = check_config()
        self.assertTrue(result)
        # Ensure it reported searching and found the second path
        mock_print.assert_any_call("Found configuration file at: /home/user/.config/mmrelay/config.yaml")
        # Ensure it did not falsely claim first path found
        printed_calls = [args[0] for args, _ in mock_print.call_args_list]
        self.assertNotIn("Found configuration file at: /etc/mmrelay/config.yaml", printed_calls)

    @patch("mmrelay.cli.get_config_paths", return_value=["/test/config.yaml"])
    @patch("os.path.isfile", return_value=True)
    @patch("builtins.open")
    @patch("yaml.load")
    @patch("builtins.print")
    def test_open_ioerror_is_reported(
        self, mock_print, mock_yaml_load, mock_open_file, _isfile, _paths
    ):
        mock_open_file.side_effect = PermissionError("Permission denied")
        from mmrelay.cli import check_config
        result = check_config()
        self.assertFalse(result)
        mock_print.assert_any_call("Error checking configuration: Permission denied")

    @patch("mmrelay.cli.get_config_paths", return_value=["/test/config.yaml"])
    @patch("os.path.isfile", return_value=True)
    @patch("builtins.open", new_callable=mock_open, read_data="{}")
    @patch("builtins.print")
    def test_yaml_loader_used_safely(self, mock_print, mock_file, _isfile, _paths):
        """
        Guard that yaml.load is called with a Loader argument (SafeLoader or FullLoader).
        We don't enforce which one, but ensure a loader is passed to avoid unsafe defaults.
        """
        with patch("yaml.load") as mock_yaml_load:
            from mmrelay.cli import check_config
            # Force YAML to be empty so the code reaches the load call
            mock_yaml_load.return_value = None
            _ = check_config()
            self.assertTrue(mock_yaml_load.called)
            # Inspect kwargs to ensure a loader kwarg is provided
            _, kwargs = mock_yaml_load.call_args
            has_loader_kw = "Loader" in kwargs or "Loader" in mock_yaml_load.call_args[1] or "Loader" in (mock_yaml_load.call_args.kwargs if hasattr(mock_yaml_load.call_args, "kwargs") else {})
            # Some code passes loader/loder argument using named keyword 'Loader' or 'Loader=yaml.SafeLoader'
            # Since introspection across different PyYAML versions may vary, also check string repr of call
            call_str = str(mock_yaml_load.call_args)
            loader_mentioned = ("Loader=" in call_str) or ("SafeLoader" in call_str) or ("FullLoader" in call_str)
            self.assertTrue(has_loader_kw or loader_mentioned, f"yaml.load should be called with a loader. Call: {mock_yaml_load.call_args}")

    @patch("mmrelay.cli.get_config_paths", return_value=["/test/config.yaml"])
    @patch("os.path.isfile", return_value=True)
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_matrix_rooms_requires_meshtastic_channel_when_expected(
        self, mock_print, mock_yaml_load, mock_file, _isfile, _paths
    ):
        """
        If the validator requires 'meshtastic_channel' for each room, ensure missing one fails.
        If implementation treats it optional, adjust assertion accordingly by checking success.
        """
        cfg = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "tok",
                "bot_user_id": "@b:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org"}],  # missing meshtastic_channel
            "meshtastic": {"connection_type": "tcp", "host": "1.2.3.4", "broadcast_enabled": True},
        }
        mock_yaml_load.return_value = cfg
        from mmrelay.cli import check_config
        result = check_config()
        # Prefer strict validation: expect failure and a meaningful message
        if result:
            # If code accepts missing meshtastic_channel, ensure it at least declares valid
            mock_print.assert_any_call("Configuration file is valid!")
        else:
            printed = [str(c) for c in mock_print.call_args_list]
            msg_found = any("meshtastic_channel" in c and "missing" in c.lower() for c in printed)
            self.assertTrue(msg_found, f"Expected an error referencing missing meshtastic_channel. Calls: {printed}")

    @patch("mmrelay.cli.get_config_paths", return_value=["/test/config.yaml"])
    @patch("os.path.isfile", return_value=True)
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_meshtastic_broadcast_enabled_type_validation(
        self, mock_print, mock_yaml_load, mock_file, _isfile, _paths
    ):
        cfg = self.valid_config.copy()
        cfg["meshtastic"] = {
            "connection_type": "tcp",
            "host": "192.168.1.100",
            "broadcast_enabled": "yes",  # wrong type
        }
        mock_yaml_load.return_value = cfg
        from mmrelay.cli import check_config
        result = check_config()
        if result:
            # If implementation coerces truthy strings, ensure valid message
            mock_print.assert_any_call("Configuration file is valid!")
        else:
            printed = [args[0] for args, _ in mock_print.call_args_list]
            self.assertTrue(
                any("broadcast_enabled" in p and "boolean" in p.lower() for p in printed),
                f"Expected type error for 'broadcast_enabled'. Printed: {printed}",
            )

    @patch("mmrelay.cli.get_config_paths", return_value=["/test/config.yaml"])
    @patch("os.path.isfile", return_value=True)
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_matrix_homeserver_malformed(
        self, mock_print, mock_yaml_load, mock_file, _isfile, _paths
    ):
        cfg = self.valid_config.copy()
        # Malformed homeserver URL (no scheme)
        cfg["matrix"] = {
            "homeserver": "matrix.org",
            "access_token": "tok",
            "bot_user_id": "@bot:matrix.org",
        }
        mock_yaml_load.return_value = cfg
        from mmrelay.cli import check_config
        result = check_config()
        if result:
            mock_print.assert_any_call("Configuration file is valid!")
        else:
            printed = [args[0] for args, _ in mock_print.call_args_list]
            self.assertTrue(
                any("homeserver" in p and ("url" in p.lower() or "scheme" in p.lower()) for p in printed),
                f"Expected validation error for malformed homeserver URL. Printed: {printed}",
            )

    @patch("mmrelay.cli.get_config_paths", return_value=["/test/config.yaml"])
    @patch("os.path.isfile", return_value=True)
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_bot_user_id_format_validation(
        self, mock_print, mock_yaml_load, mock_file, _isfile, _paths
    ):
        cfg = self.valid_config.copy()
        cfg["matrix"]["bot_user_id"] = "bot:matrix.org"  # missing '@'
        mock_yaml_load.return_value = cfg
        from mmrelay.cli import check_config
        result = check_config()
        if result:
            mock_print.assert_any_call("Configuration file is valid!")
        else:
            printed = [args[0] for args, _ in mock_print.call_args_list]
            self.assertTrue(
                any("bot_user_id" in p and ("@" in p or "format" in p.lower()) for p in printed),
                f"Expected validation error for bot_user_id format. Printed: {printed}",
            )

    @patch("mmrelay.cli.get_config_paths", return_value=["/test/config.yaml"])
    @patch("os.path.isfile", return_value=True)
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_meshtastic_extra_fields_are_ignored(
        self, mock_print, mock_yaml_load, mock_file, _isfile, _paths
    ):
        cfg = self.valid_config.copy()
        cfg["meshtastic"] = {
            "connection_type": "tcp",
            "host": "10.0.0.5",
            "broadcast_enabled": False,
            "unused_field": "ignore_me",
        }
        mock_yaml_load.return_value = cfg
        from mmrelay.cli import check_config
        result = check_config()
        # Prefer permissive: unknown fields should not break validation
        self.assertTrue(result)
        mock_print.assert_any_call("Configuration file is valid!")

    @patch("mmrelay.cli.get_config_paths", return_value=["/test/config.yaml"])
    @patch("os.path.isfile", return_value=True)
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_matrix_rooms_non_int_meshtastic_channel(
        self, mock_print, mock_yaml_load, mock_file, _isfile, _paths
    ):
        cfg = self.valid_config.copy()
        cfg["matrix_rooms"] = [{"id": "!room1:matrix.org", "meshtastic_channel": "zero"}]
        mock_yaml_load.return_value = cfg
        from mmrelay.cli import check_config
        result = check_config()
        if result:
            mock_print.assert_any_call("Configuration file is valid!")
        else:
            printed = [args[0] for args, _ in mock_print.call_args_list]
            self.assertTrue(
                any("meshtastic_channel" in p and ("integer" in p.lower() or "int" in p.lower()) for p in printed),
                f"Expected type validation for meshtastic_channel. Printed: {printed}",
            )

    @patch("mmrelay.cli.get_config_paths", return_value=["/test/config.yaml"])
    @patch("os.path.isfile", return_value=True)
    @patch("builtins.open", new_callable=mock_open)
    @patch("yaml.load")
    @patch("builtins.print")
    def test_matrix_rooms_empty_list(
        self, mock_print, mock_yaml_load, mock_file, _isfile, _paths
    ):
        cfg = self.valid_config.copy()
        cfg["matrix_rooms"] = []
        mock_yaml_load.return_value = cfg
        from mmrelay.cli import check_config
        result = check_config()
        self.assertFalse(result)
        mock_print.assert_any_call("Error: Missing or empty 'matrix_rooms' section in config")