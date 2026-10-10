#!/usr/bin/env python3
"""
Test suite for the MMRelay nodes plugin.

Tests the node listing functionality including:
- Relative time calculations
- Node data formatting and display
- Meshtastic client integration
- Matrix room message handling
- Device metrics parsing
"""

import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.constants.formats import DATE_FORMAT_LONG
from mmrelay.plugins.nodes_plugin import (
    DEFAULT_MAX_RESULTS,
    FIELD_PATHS,
    USAGE_TEXT,
    NodesQuery,
    NodesUsageError,
    Plugin,
    _format_last_seen,
    _is_sensitive_field_path,
    _last_heard_sort_value,
    get_relative_time,
    parse_nodes_args,
)


class TestGetRelativeTime(unittest.TestCase):
    """Test cases for the get_relative_time utility function."""

    FIXED_NOW = datetime(2026, 3, 26, 12, 0, 0)

    def test_get_relative_time_just_now(self):
        """
        Test that `get_relative_time` returns "Just now" for timestamps within a few seconds of the current time.
        """
        # Note: Uses real datetime.now() since the function is designed to handle
        # timestamps within a few seconds of current time reliably
        now = datetime.now()
        timestamp = now.timestamp()

        result = get_relative_time(timestamp)

        self.assertEqual(result, "Just now")

    def test_get_relative_time_minutes_ago(self):
        """
        Tests that `get_relative_time` returns the correct string for a timestamp five minutes ago.
        """
        now = datetime.now()
        five_minutes_ago = now - timedelta(minutes=5)
        timestamp = five_minutes_ago.timestamp()

        result = get_relative_time(timestamp)

        self.assertEqual(result, "5 minutes ago")

    def test_get_relative_time_one_minute_ago(self):
        """Test relative time for exactly one minute ago."""
        now = datetime.now()
        one_minute_ago = now - timedelta(minutes=1)
        timestamp = one_minute_ago.timestamp()

        result = get_relative_time(timestamp)

        self.assertEqual(result, "1 minute ago")

    def test_get_relative_time_hours_ago(self):
        """
        Test that `get_relative_time` returns the correct string for a timestamp three hours ago.
        """
        now = datetime.now()
        three_hours_ago = now - timedelta(hours=3)
        timestamp = three_hours_ago.timestamp()

        result = get_relative_time(timestamp)

        self.assertEqual(result, "3 hours ago")

    def test_get_relative_time_one_hour_ago(self):
        """Test relative time for exactly one hour ago."""
        now = datetime.now()
        one_hour_ago = now - timedelta(hours=1)
        timestamp = one_hour_ago.timestamp()

        result = get_relative_time(timestamp)

        self.assertEqual(result, "1 hour ago")

    def test_get_relative_time_days_ago(self):
        """
        Tests that `get_relative_time` returns the correct string for a timestamp three days ago.
        """
        now = datetime.now()
        three_days_ago = now - timedelta(days=3)
        timestamp = three_days_ago.timestamp()

        result = get_relative_time(timestamp)

        self.assertEqual(result, "3 days ago")

    def test_get_relative_time_one_day_ago(self):
        """Test relative time for exactly one day ago."""
        now = datetime.now()
        one_day_ago = now - timedelta(days=1)
        timestamp = one_day_ago.timestamp()

        result = get_relative_time(timestamp)

        self.assertEqual(result, "1 day ago")

    def test_get_relative_time_old_date(self):
        """
        Test that `get_relative_time` returns a formatted date string for timestamps older than 7 days.
        """
        now = datetime.now()
        ten_days_ago = now - timedelta(days=10)
        timestamp = ten_days_ago.timestamp()

        result = get_relative_time(timestamp)

        # Should return formatted date like "Jan 15, 2024"
        expected_format = ten_days_ago.strftime(DATE_FORMAT_LONG)
        self.assertEqual(result, expected_format)

    def test_get_relative_time_exactly_seven_days(self):
        """
        Test that `get_relative_time` returns "7 days ago" for a timestamp exactly seven days in the past.
        """
        seven_days_ago = self.FIXED_NOW - timedelta(days=7)
        timestamp = seven_days_ago.timestamp()

        with patch("mmrelay.plugins.nodes_plugin.datetime") as mock_datetime:
            mock_datetime.now.return_value = self.FIXED_NOW
            mock_datetime.fromtimestamp.side_effect = datetime.fromtimestamp
            result = get_relative_time(timestamp)

        self.assertEqual(result, "7 days ago")

    def test_get_relative_time_exactly_eight_days(self):
        """Test that get_relative_time returns a formatted date string for a timestamp exactly eight days ago."""
        eight_days_ago = self.FIXED_NOW - timedelta(days=8)
        timestamp = eight_days_ago.timestamp()

        with patch("mmrelay.plugins.nodes_plugin.datetime") as mock_datetime:
            mock_datetime.now.return_value = self.FIXED_NOW
            mock_datetime.fromtimestamp.side_effect = datetime.fromtimestamp
            result = get_relative_time(timestamp)

        expected_format = eight_days_ago.strftime(DATE_FORMAT_LONG)
        self.assertEqual(result, expected_format)


class TestNodesPlugin(unittest.TestCase):
    """Test cases for the nodes plugin."""

    def setUp(self):
        """
        Initialize the test environment with a mocked Plugin instance and a Meshtastic client containing sample node data for testing.

        Creates a Plugin object with mocked logger and asynchronous message sending. Sets up a mock Meshtastic client with three nodes, each having varying completeness of user, SNR, lastHeard, and deviceMetrics data.
        """
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()

        # Mock Matrix client methods
        self.plugin.send_matrix_message = AsyncMock()
        self.plugin.send_matrix_reaction = AsyncMock()

        # Mock meshtastic client with sample node data
        self.mock_meshtastic_client = MagicMock()
        self.mock_meshtastic_client.nodes = {
            "node1": {
                "user": {
                    "shortName": "N1",
                    "longName": "Node One",
                    "hwModel": "HELTEC_V3",
                },
                "snr": 12.5,
                "lastHeard": (datetime.now() - timedelta(minutes=5)).timestamp(),
                "deviceMetrics": {"voltage": 4.2, "batteryLevel": 85},
            },
            "node2": {
                "user": {"shortName": "N2", "longName": "Node Two", "hwModel": "TBEAM"},
                "snr": -8.0,
                "lastHeard": (datetime.now() - timedelta(hours=2)).timestamp(),
                "deviceMetrics": {"voltage": 3.8, "batteryLevel": 45},
            },
            "node3": {
                "user": {
                    "shortName": "N3",
                    "longName": "Node Three",
                    "hwModel": "LORA32_V2_1",
                }
                # No SNR, lastHeard, or deviceMetrics data
            },
        }

    def test_plugin_name(self):
        """
        Verify that the plugin's name attribute is set to "nodes".
        """
        self.assertEqual(self.plugin.plugin_name, "nodes")

    def test_description_property(self):
        """
        Verify that the plugin description documents configurable node fields.
        """
        description = self.plugin.description

        self.assertIn("Show mesh radios and node data", description)
        self.assertIn("plugins.nodes.fields", description)
        self.assertIn("status", description)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_with_full_data(self, mock_connect):
        """
        Test that `generate_response` produces correct output when all node data fields are present.

        Verifies that the response includes node count, names, hardware models, battery and voltage information, SNR values, and relative time phrases for nodes with complete data.
        """
        mock_connect.return_value = self.mock_meshtastic_client

        response = self.plugin.generate_response()

        # Should start with node count
        self.assertIn("Nodes: 3", response)

        # Should contain node information
        self.assertIn("N1 Node One", response)
        self.assertIn("N2 Node Two", response)
        self.assertIn("N3 Node Three", response)

        # Should contain hardware models
        self.assertIn("HELTEC_V3", response)
        self.assertIn("TBEAM", response)
        self.assertIn("LORA32_V2_1", response)

        # Should contain battery and voltage info for nodes with data
        self.assertIn("85% 4.2V", response)
        self.assertIn("45% 3.8V", response)

        # Should contain SNR info for nodes with data
        self.assertIn("12.5 dB", response)
        self.assertIn("-8.0 dB", response)

        # Should contain relative time info
        self.assertIn("minutes ago", response)
        self.assertIn("hours ago", response)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_with_missing_data(self, mock_connect):
        """
        Test that the response generated by the plugin correctly handles nodes with missing data fields.

        Verifies that the output includes appropriate placeholders for missing battery, voltage, and last heard time, and still displays available node information.
        """
        # Create a client with minimal node data
        minimal_client = MagicMock()
        minimal_client.nodes = {
            "node_minimal": {
                "user": {
                    "shortName": "MIN",
                    "longName": "Minimal Node",
                    "hwModel": "UNKNOWN",
                }
                # No SNR, lastHeard, or deviceMetrics
            }
        }
        mock_connect.return_value = minimal_client

        response = self.plugin.generate_response()

        # Should handle missing data gracefully
        self.assertIn("Nodes: 1", response)
        self.assertIn("MIN Minimal Node", response)
        self.assertIn("UNKNOWN", response)
        self.assertIn("?% ?V", response)  # Default values for missing battery/voltage
        self.assertIn("/ ?", response)  # No last heard time

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_missing_hw_model_defaults_unknown(self, mock_connect):
        """
        Test that missing hwModel in user data does not raise and renders as Unknown.
        """
        client = MagicMock()
        client.nodes = {
            "node_no_hw": {
                "user": {
                    "shortName": "NHW",
                    "longName": "No Hw Model",
                }
            }
        }
        mock_connect.return_value = client

        response = self.plugin.generate_response()

        self.assertIn("NHW No Hw Model / Unknown", response)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_missing_user_defaults_unknown(self, mock_connect):
        """
        Test that nodes without a user block are rendered with Unknown placeholders.
        """
        client = MagicMock()
        client.nodes = {"node_no_user": {"snr": 5.0}}
        mock_connect.return_value = client

        response = self.plugin.generate_response()

        self.assertIn("Unknown Unknown / Unknown", response)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_skips_non_dict_node_info(self, mock_connect):
        """
        Test that malformed non-dict node entries are skipped without errors.
        """
        client = MagicMock()
        client.nodes = {
            "node_good": {"user": {"shortName": "OK", "longName": "Valid"}},
            "node_bad": "not-a-dict",
        }
        mock_connect.return_value = client

        response = self.plugin.generate_response()

        self.assertIn("Nodes: 1", response)
        self.assertIn("OK Valid / Unknown", response)
        self.assertNotIn("not-a-dict", response)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_invalid_last_heard_falls_back_unknown(
        self, mock_connect
    ):
        """
        Test that invalid lastHeard values are safely rendered as unknown.
        """
        client = MagicMock()
        client.nodes = {
            "node_bad_lastheard": {
                "user": {
                    "shortName": "BAD",
                    "longName": "Bad LastHeard",
                    "hwModel": "TEST",
                },
                "lastHeard": "not-a-timestamp",
            }
        }
        mock_connect.return_value = client

        response = self.plugin.generate_response()

        self.assertIn("Nodes: 1", response)
        self.assertIn("BAD Bad LastHeard / TEST", response)
        self.assertIn("/ ?", response)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_with_null_values(self, mock_connect):
        """
        Test that the response generated by the plugin correctly handles nodes with null values for SNR, lastHeard, voltage, and batteryLevel, using default placeholders where appropriate.
        """
        null_client = MagicMock()
        null_client.nodes = {
            "node_null": {
                "user": {
                    "shortName": "NULL",
                    "longName": "Null Node",
                    "hwModel": "TEST",
                },
                "snr": None,
                "lastHeard": None,
                "deviceMetrics": {"voltage": None, "batteryLevel": None},
            }
        }
        mock_connect.return_value = null_client

        response = self.plugin.generate_response()

        # Should handle null values gracefully
        self.assertIn("Nodes: 1", response)
        self.assertIn("NULL Null Node", response)
        self.assertIn("?% ?V", response)  # Default values for null battery/voltage
        self.assertIn("/ ?", response)  # No last heard time

    def test_get_relative_time_future_timestamp(self):
        """Future timestamps should return 'Just now' (line 73)."""
        future_ts = (datetime.now() + timedelta(hours=1)).timestamp()
        result = get_relative_time(future_ts)
        self.assertEqual(result, "Just now")

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_no_client(self, mock_connect):
        """generate_response returns error when client is None (line 108)."""
        mock_connect.return_value = None
        response = self.plugin.generate_response()
        self.assertEqual(response, "Unable to connect to Meshtastic device.")

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_zero_last_heard(self, mock_connect):
        """lastHeard of 0 should render as '?' (line 143->151)."""
        client = MagicMock()
        client.nodes = {
            "node_zero_lh": {
                "user": {"shortName": "ZER", "longName": "Zero LH", "hwModel": "T"},
                "lastHeard": 0,
            }
        }
        mock_connect.return_value = client
        response = self.plugin.generate_response()
        self.assertIn("ZER Zero LH", response)
        self.assertIn("/ ?", response)

    def test_handle_meshtastic_message_always_false(self):
        """
        Test that handle_meshtastic_message always returns False regardless of input.
        """

        async def run_test() -> None:
            """
            Asynchronously tests that handle_meshtastic_message always returns False.
            """
            result = await self.plugin.handle_meshtastic_message(
                {}, "formatted_message", "longname", "meshnet_name"
            )
            self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    def test_handle_room_message_no_match(self):
        """
        Test that handle_room_message returns False and does not send a message when the event does not match.
        """
        self.plugin.get_matching_matrix_command_with_args = MagicMock(return_value=None)

        room = MagicMock()
        event = MagicMock()

        async def run_test() -> None:
            """
            Asynchronously tests that handle_room_message returns False and does not send a Matrix message when the event does not match.

            Verifies that the command parser returns None and send_matrix_message is not called.
            """
            result = await self.plugin.handle_room_message(room, event, "full_message")
            self.assertFalse(result)
            self.plugin.send_matrix_message.assert_not_called()
            self.plugin.send_matrix_reaction.assert_not_called()

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_handle_room_message_with_match(self, mock_connect):
        """
        Tests that handle_room_message sends a Matrix message and returns True when the event matches, verifying correct message content and parameters.
        """
        mock_connect.return_value = self.mock_meshtastic_client
        self.plugin.get_matching_matrix_command_with_args = MagicMock(
            return_value=("nodes", "")
        )

        room = MagicMock()
        room.room_id = "!test:matrix.org"
        event = MagicMock()

        async def run_test() -> None:
            """
            Asynchronously tests that a room message matching the plugin's criteria triggers a Matrix message with correct node information.

            Returns:
                bool: True if the plugin handled the room message and sent a Matrix message.
            """
            result = await self.plugin.handle_room_message(room, event, "full_message")

            self.assertTrue(result)
            self.plugin.send_matrix_message.assert_called_once()

            # Check the call arguments
            call_args = self.plugin.send_matrix_message.call_args
            self.assertEqual(call_args.kwargs["room_id"], "!test:matrix.org")
            self.assertIn("Nodes: 3", call_args.kwargs["message"])
            self.assertEqual(call_args.kwargs["formatted"], False)

            self.plugin.send_matrix_reaction.assert_called_once_with(
                "!test:matrix.org", event.event_id, "✅"
            )

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_hop_count_zero(self, mock_connect):
        """
        Test that generate_response displays 'direct' for nodes with hopsAway: 0.
        """
        hop_client = MagicMock()
        hop_client.nodes = {
            "node_direct": {
                "user": {
                    "shortName": "DIR",
                    "longName": "Direct Node",
                    "hwModel": "TEST",
                },
                "hopsAway": 0,
            }
        }
        mock_connect.return_value = hop_client

        response = self.plugin.generate_response()

        self.assertIn("Nodes: 1", response)
        self.assertIn("DIR Direct Node", response)
        self.assertIn("direct", response)
        self.assertNotIn("? hops away", response)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_hop_count_one(self, mock_connect):
        """
        Test that generate_response displays '1 hop away' for nodes with hopsAway: 1.
        """
        hop_client = MagicMock()
        hop_client.nodes = {
            "node_one_hop": {
                "user": {
                    "shortName": "ONE",
                    "longName": "One Hop Node",
                    "hwModel": "TEST",
                },
                "hopsAway": 1,
            }
        }
        mock_connect.return_value = hop_client

        response = self.plugin.generate_response()

        self.assertIn("Nodes: 1", response)
        self.assertIn("ONE One Hop Node", response)
        self.assertIn("1 hop away", response)
        self.assertNotIn("? hops away", response)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_hop_count_multiple(self, mock_connect):
        """
        Test that generate_response displays 'N hops away' for nodes with hopsAway > 1.
        """
        hop_client = MagicMock()
        hop_client.nodes = {
            "node_four_hops": {
                "user": {
                    "shortName": "FOUR",
                    "longName": "Four Hops Node",
                    "hwModel": "TEST",
                },
                "hopsAway": 4,
            }
        }
        mock_connect.return_value = hop_client

        response = self.plugin.generate_response()

        self.assertIn("Nodes: 1", response)
        self.assertIn("FOUR Four Hops Node", response)
        self.assertIn("4 hops away", response)
        self.assertNotIn("? hops away", response)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_hop_count_missing(self, mock_connect):
        """
        Test that generate_response displays '? hops away' for nodes without hopsAway field.
        """
        hop_client = MagicMock()
        hop_client.nodes = {
            "node_no_hops": {
                "user": {
                    "shortName": "NOP",
                    "longName": "No Hops Node",
                    "hwModel": "TEST",
                }
                # No hopsAway field
            }
        }
        mock_connect.return_value = hop_client

        response = self.plugin.generate_response()

        self.assertIn("Nodes: 1", response)
        self.assertIn("NOP No Hops Node", response)
        self.assertIn("? hops away", response)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_hop_count_null(self, mock_connect):
        """
        Test that generate_response displays '? hops away' for nodes with hopsAway: None.
        """
        hop_client = MagicMock()
        hop_client.nodes = {
            "node_null_hops": {
                "user": {
                    "shortName": "NUL",
                    "longName": "Null Hops Node",
                    "hwModel": "TEST",
                },
                "hopsAway": None,
            }
        }
        mock_connect.return_value = hop_client

        response = self.plugin.generate_response()

        self.assertIn("Nodes: 1", response)
        self.assertIn("NUL Null Hops Node", response)
        self.assertIn("? hops away", response)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_mixed_hop_counts(self, mock_connect):
        """
        Test that generate_response correctly handles a mix of different hop count scenarios.
        """
        mixed_client = MagicMock()
        mixed_client.nodes = {
            "node_direct": {
                "user": {
                    "shortName": "DIR",
                    "longName": "Direct Node",
                    "hwModel": "TEST",
                },
                "hopsAway": 0,
            },
            "node_one_hop": {
                "user": {
                    "shortName": "ONE",
                    "longName": "One Hop Node",
                    "hwModel": "TEST",
                },
                "hopsAway": 1,
            },
            "node_multi_hop": {
                "user": {
                    "shortName": "MUL",
                    "longName": "Multi Hop Node",
                    "hwModel": "TEST",
                },
                "hopsAway": 3,
            },
            "node_missing": {
                "user": {
                    "shortName": "MIS",
                    "longName": "Missing Hops Node",
                    "hwModel": "TEST",
                }
                # No hopsAway field
            },
            "node_null": {
                "user": {
                    "shortName": "NUL",
                    "longName": "Null Hops Node",
                    "hwModel": "TEST",
                },
                "hopsAway": None,
            },
        }
        mock_connect.return_value = mixed_client

        response = self.plugin.generate_response()

        self.assertIn("Nodes: 5", response)
        # Check each hop scenario
        self.assertIn("DIR Direct Node", response)
        self.assertIn("direct", response)

        self.assertIn("ONE One Hop Node", response)
        self.assertIn("1 hop away", response)

        self.assertIn("MUL Multi Hop Node", response)
        self.assertIn("3 hops away", response)

        self.assertIn("MIS Missing Hops Node", response)
        self.assertIn("NUL Null Hops Node", response)
        # Should have two instances of "? hops away"
        self.assertEqual(response.count("? hops away"), 2)

    def test_handle_room_message_exception_handler(self):
        """Test exception handler in handle_room_message."""
        self.plugin.get_matching_matrix_command_with_args = MagicMock(
            return_value=("nodes", "")
        )
        self.plugin.generate_response = MagicMock(side_effect=RuntimeError("boom"))

        room = MagicMock()
        room.room_id = "!test:matrix.org"
        event = MagicMock()

        async def run_test() -> None:
            result = await self.plugin.handle_room_message(room, event, "full_message")
            self.assertTrue(result)
            self.plugin.logger.exception.assert_called_once_with(
                "Error handling nodes command"
            )
            self.plugin.send_matrix_reaction.assert_called_once_with(
                "!test:matrix.org", event.event_id, "❌"
            )

        import asyncio

        asyncio.run(run_test())


@pytest.fixture
def feature_plugin() -> Plugin:
    """Provide an isolated nodes plugin for feature-focused pytest coverage."""
    plugin = Plugin()
    plugin.logger = MagicMock()
    plugin.send_matrix_message = AsyncMock()
    plugin.send_matrix_reaction = AsyncMock()
    return plugin


@pytest.fixture
def feature_meshtastic_client() -> MagicMock:
    """Provide representative node data for configurable-field tests."""
    client = MagicMock()
    client.nodes = {
        "node1": {
            "user": {
                "shortName": "N1",
                "longName": "Node One",
                "hwModel": "HELTEC_V3",
            },
            "snr": 12.5,
            "lastHeard": (datetime.now() - timedelta(minutes=5)).timestamp(),
            "deviceMetrics": {"voltage": 4.2, "batteryLevel": 85},
        },
        "node2": {
            "user": {"shortName": "N2", "longName": "Node Two", "hwModel": "TBEAM"},
            "snr": -8.0,
            "lastHeard": (datetime.now() - timedelta(hours=2)).timestamp(),
            "deviceMetrics": {"voltage": 3.8, "batteryLevel": 45},
        },
        "node3": {
            "user": {
                "shortName": "N3",
                "longName": "Node Three",
                "hwModel": "LORA32_V2_1",
            }
        },
    }
    return client


def test_generate_response_includes_status_when_available(
    feature_plugin: Plugin, feature_meshtastic_client: MagicMock
) -> None:
    """Firmware 2.8 status messages appear in the default view when cached."""
    feature_meshtastic_client.nodes["node1"]["status"] = "At the trailhead"
    with patch(
        "mmrelay.meshtastic_utils.connect_meshtastic",
        return_value=feature_meshtastic_client,
    ):
        response = feature_plugin.generate_response()
    assert "status: At the trailhead" in response


def test_generate_response_supports_configured_fields(feature_plugin: Plugin) -> None:
    """Configured aliases and raw dotted paths render in configured order."""
    client = MagicMock()
    client.nodes = {
        "!12345678": {
            "num": 0x12345678,
            "user": {
                "id": "!12345678",
                "publicKey": bytes(range(32)),
                "role": "ROUTER",
            },
            "status": "Relay online",
            "environmentMetrics": {"temperature": 21.5},
        }
    }
    feature_plugin.config["fields"] = [
        "node_id",
        "role",
        "status",
        "public_key",
        "environmentMetrics.temperature",
    ]
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        response = feature_plugin.generate_response()
    expected_key = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
    assert response.splitlines()[1] == (
        "id: !12345678 / role: ROUTER / status: Relay online / "
        f"key: {expected_key} / environmentMetrics.temperature: 21.5"
    )


@pytest.mark.parametrize(
    "invalid_last_heard", [float("inf"), float("-inf"), float("nan"), 0, -1]
)
def test_generate_response_sorts_invalid_timestamps_with_unknowns(
    feature_plugin: Plugin, invalid_last_heard: float
) -> None:
    """Non-finite and non-positive timestamps sort with unknown timestamps."""
    client = MagicMock()
    client.nodes = {
        "invalid": {
            "user": {"shortName": "BAD", "longName": "Invalid"},
            "lastHeard": invalid_last_heard,
        },
        "valid": {
            "user": {"shortName": "NEW", "longName": "Valid"},
            "lastHeard": 200,
        },
        "unknown": {"user": {"shortName": "UNK", "longName": "Unknown"}},
    }
    feature_plugin.config["fields"] = ["name", "last_seen"]
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        response = feature_plugin.generate_response().splitlines()
    assert response[1].startswith("NEW Valid / ")
    assert set(response[2:]) == {"BAD Invalid / ?", "UNK Unknown / ?"}


def test_generate_response_sorts_nodes_by_recency(feature_plugin: Plugin) -> None:
    """The most recently heard node is listed first."""
    client = MagicMock()
    client.nodes = {
        "older": {
            "user": {"shortName": "OLD", "longName": "Older"},
            "lastHeard": 100,
        },
        "newer": {
            "user": {"shortName": "NEW", "longName": "Newer"},
            "lastHeard": 200,
        },
        "unknown": {"user": {"shortName": "UNK", "longName": "Unknown"}},
    }
    feature_plugin.config["fields"] = ["name"]
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        response = feature_plugin.generate_response().splitlines()
    assert response[1:] == ["NEW Newer", "OLD Older", "UNK Unknown"]


def test_generate_response_marks_nodes_with_no_renderable_fields(
    feature_plugin: Plugin,
) -> None:
    """Nodes remain countable when selected data has not been reported."""
    client = MagicMock()
    client.nodes = {"node1": {"user": {"shortName": "N1"}}}
    feature_plugin.config["fields"] = ["status"]
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        response = feature_plugin.generate_response()
    assert response == "Nodes: 1\nNo fields available\n"


def test_generate_response_formats_supported_custom_field_types(
    feature_plugin: Plugin,
) -> None:
    """Configured aliases format metrics, location, flags, keys, and raw values."""
    client = MagicMock()
    client.nodes = {
        "!deadbeef": {
            "user": {"publicKey": bytearray(b"key")},
            "deviceMetrics": {
                "batteryLevel": 91,
                "voltage": 4.1,
                "channelUtilization": 12.5,
                "airUtilTx": 3.25,
                "uptimeSeconds": 42,
            },
            "position": {"latitude": 1.5, "longitude": -2.5, "altitude": 123},
            "isFavorite": False,
            "rawBytes": b"\x00\xff",
            "rawBytearray": bytearray(b"ab"),
            "rawBool": True,
        }
    }
    feature_plugin.config["fields"] = [
        "node_id",
        "public_key",
        "battery",
        "voltage",
        "channel_utilization",
        "air_util_tx",
        "uptime",
        "latitude",
        "longitude",
        "altitude",
        "favorite",
        "rawBytes",
        "rawBytearray",
        "rawBool",
    ]
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        response = feature_plugin.generate_response()
    for expected in (
        "id: !deadbeef",
        "key: a2V5",
        "battery: 91%",
        "voltage: 4.1V",
        "channel util: 12.5%",
        "air util tx: 3.25%",
        "uptime: 42s",
        "lat: 1.5°",
        "lon: -2.5°",
        "alt: 123m",
        "favorite: no",
        "rawBytes: AP8=",
        "rawBytearray: YWI=",
        "rawBool: yes",
    ):
        assert expected in response


@pytest.mark.parametrize("fields", ["status", [None, 1, "   "]])
def test_invalid_field_configurations_fall_back_to_defaults(
    feature_plugin: Plugin, fields: object
) -> None:
    """Malformed or empty field selections retain the established default view."""
    client = MagicMock()
    client.nodes = {"node1": {"user": {"shortName": "N1", "longName": "Node"}}}
    feature_plugin.config["fields"] = fields
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        response = feature_plugin.generate_response()
    assert "N1 Node / Unknown" in response
    feature_plugin.logger.warning.assert_called_once()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("already-encoded", "already-encoded"),
        ("", None),
        (123, "123"),
        (None, None),
        (b"", None),
        (bytearray(), None),
    ],
)
def test_public_key_formatter_accepts_string_and_scalar_values(
    value: object, expected: str | None
) -> None:
    """Public-key rendering tolerates alternate serialized node sources."""
    from mmrelay.plugins.nodes_plugin import _format_public_key

    assert _format_public_key(value) == expected


def test_relative_time_under_one_minute_is_just_now() -> None:
    """A recent past timestamp takes the final sub-minute branch."""
    timestamp = (datetime.now() - timedelta(seconds=30)).timestamp()
    assert get_relative_time(timestamp) == "Just now"


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), 0, -1])
def test_invalid_last_heard_values_are_unknown(value: float) -> None:
    """Invalid timestamps render and sort like unknown timestamps."""
    assert _last_heard_sort_value({"lastHeard": value}) == 0
    assert _format_last_seen(value) == "?"


@pytest.mark.parametrize(
    "field_path",
    [
        "config.security.privateKey",
        "config.security.private_key",
        "network.wifiPsk",
        "network.wifi_ssid",
        "wifiPassword",
        "psk",
        "channelSettings.psk",
        "config.security.adminKey",
        "config.security.admin_key",
        "config.security.sessionKey",
        "adminSessionPassKey",
        "admin.session_passkey",
        "someSecretValue",
        "bluetooth.fixedPin",
    ],
)
def test_sensitive_field_paths_are_detected(field_path: str) -> None:
    """Secret-bearing dotted paths are flagged regardless of spelling."""
    assert _is_sensitive_field_path(field_path) is True


@pytest.mark.parametrize(
    "field_path",
    [
        "user.publicKey",
        "user.public_key",
        "user.hwModel",
        "deviceMetrics.batteryLevel",
        "environmentMetrics.temperature",
        "position.latitude",
        "lastHeard",
        "hopsAway",
        "status",
    ],
)
def test_public_data_field_paths_are_allowed(field_path: str) -> None:
    """Public node data paths, including public keys, stay renderable."""
    assert _is_sensitive_field_path(field_path) is False


def test_all_field_aliases_avoid_sensitive_tokens() -> None:
    """Every alias the plugin resolves must survive the sensitive-path screen."""
    assert all(not _is_sensitive_field_path(path) for path in FIELD_PATHS.values())


def test_generate_response_withholds_configured_secret_fields(
    feature_plugin: Plugin,
) -> None:
    """Secret-bearing configured fields are skipped and logged, not rendered."""
    client = MagicMock()
    client.nodes = {
        "node1": {
            "user": {
                "shortName": "SEC",
                "longName": "Secret Holder",
                "privateKey": b"super-secret-key",
            },
            "config": {"security": {"privateKey": b"super-secret-key"}},
        }
    }
    feature_plugin.config["fields"] = [
        "name",
        "user.privateKey",
        "config.security.privateKey",
    ]
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        response = feature_plugin.generate_response()
    assert "SEC Secret Holder" in response
    assert "super-secret-key" not in response
    feature_plugin.logger.warning.assert_called_once()
    warned_args = feature_plugin.logger.warning.call_args.args
    assert "user.privateKey" in warned_args[1]
    assert "config.security.privateKey" in warned_args[1]


def test_generate_response_all_sensitive_fields_use_defaults(
    feature_plugin: Plugin,
) -> None:
    """A fully sensitive field selection falls back to the default view."""
    client = MagicMock()
    client.nodes = {
        "node1": {
            "user": {"shortName": "N1", "longName": "Node", "hwModel": "TBEAM"},
            "psk": b"not-for-matrix",
        }
    }
    feature_plugin.config["fields"] = ["psk", "wifiPassword"]
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        response = feature_plugin.generate_response()
    assert "N1 Node / TBEAM" in response
    assert "not-for-matrix" not in response
    assert feature_plugin.logger.warning.call_count == 2


def test_generate_response_does_not_dump_container_values(
    feature_plugin: Plugin,
) -> None:
    """Bare parent paths render nothing instead of stringifying nested dicts."""
    client = MagicMock()
    client.nodes = {
        "node1": {
            "user": {
                "shortName": "DICT",
                "longName": "Dict Node",
                "privateKey": b"nested-secret",
            }
        }
    }
    feature_plugin.config["fields"] = ["user", "name"]
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        response = feature_plugin.generate_response()
    assert "DICT Dict Node" in response
    assert "{" not in response
    assert "nested-secret" not in response


def test_generate_response_withholds_cached_admin_session_passkey(
    feature_plugin: Plugin,
) -> None:
    client = MagicMock()
    client.nodes = {
        "node1": {"status": "Ready", "adminSessionPassKey": b"session-token"}
    }
    feature_plugin.config["fields"] = ["status", "adminSessionPassKey"]
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        response = feature_plugin.generate_response()
    assert response == "Nodes: 1\nstatus: Ready\n"


@pytest.mark.parametrize("container", [{"privateKey": "nested-secret"}, ["secret"]])
def test_default_name_and_power_fields_do_not_dump_containers(
    feature_plugin: Plugin, container: object
) -> None:
    client = MagicMock()
    client.nodes = {
        "node1": {
            "user": {"shortName": container, "longName": container},
            "deviceMetrics": {"batteryLevel": container, "voltage": container},
        }
    }
    feature_plugin.config["fields"] = ["name", "power"]
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        response = feature_plugin.generate_response()
    assert response == "Nodes: 1\nUnknown Unknown / ?% ?V\n"


def test_empty_status_is_omitted(feature_plugin: Plugin) -> None:
    client = MagicMock()
    client.nodes = {"node1": {"status": ""}}
    feature_plugin.config["fields"] = ["status"]
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        response = feature_plugin.generate_response()
    assert response == "Nodes: 1\nNo fields available\n"


class TestParseNodesArgs(unittest.TestCase):
    """Test cases for the !nodes argument parser."""

    def test_empty_args_yield_default_query(self):
        """No arguments means no limit, no sort, no filters, no field override."""
        self.assertEqual(parse_nodes_args(""), NodesQuery())
        self.assertEqual(parse_nodes_args("   "), NodesQuery())

    def test_bare_number_is_limit_shorthand(self):
        """A lone digit token is accepted as a limit override."""
        self.assertEqual(parse_nodes_args("50").limit, 50)
        self.assertEqual(parse_nodes_args("0").limit, 0)

    def test_limit_keyword(self):
        """'limit N' parses; 'limit all' and 'limit 0' both mean unlimited."""
        self.assertEqual(parse_nodes_args("limit 5").limit, 5)
        self.assertEqual(parse_nodes_args("limit all").limit, 0)
        self.assertEqual(parse_nodes_args("LIMIT ALL").limit, 0)

    def test_limit_rejects_non_numbers(self):
        """'limit' accepts only a non-negative integer or 'all'."""
        for bad in ("limit -1", "limit xyz", "limit 3.5", "limit"):
            with self.assertRaises(NodesUsageError):
                parse_nodes_args(bad)

    def test_sort_field_direction_and_by(self):
        """'sort <field>' parses with optional 'by' and asc/desc suffixes."""
        self.assertEqual(
            parse_nodes_args("sort snr"),
            NodesQuery(sort_field="snr", sort_direction=None),
        )
        self.assertEqual(
            parse_nodes_args("sort by name"),
            NodesQuery(sort_field="name", sort_direction=None),
        )
        self.assertEqual(
            parse_nodes_args("SORT By SNR DESC"),
            NodesQuery(sort_field="snr", sort_direction="desc"),
        )

    def test_repeated_sort_wins_last(self):
        """A later sort overrides the field, direction, and any earlier direction."""
        self.assertEqual(
            parse_nodes_args("sort name desc sort snr"),
            NodesQuery(sort_field="snr", sort_direction=None),
        )

    def test_sort_requires_known_field(self):
        """Unknown sort fields and a missing field raise usage errors."""
        for bad in ("sort", "sort by", "sort bogus"):
            with self.assertRaises(NodesUsageError):
                parse_nodes_args(bad)

    def test_fields_override_comma_separated(self):
        """'fields a,b' selects display fields; empty parts are dropped."""
        self.assertEqual(
            parse_nodes_args("fields battery,uptime").display_fields,
            ("battery", "uptime"),
        )
        self.assertEqual(
            parse_nodes_args("FIELDS Battery,UPTime").display_fields,
            ("battery", "uptime"),
        )
        with self.assertRaises(NodesUsageError):
            parse_nodes_args("fields")
        with self.assertRaises(NodesUsageError):
            parse_nodes_args("fields ,,")

    def test_filter_field_normalized_and_values_kept(self):
        """Field aliases are lowercased; filter values keep their spelling."""
        self.assertEqual(
            parse_nodes_args("ROLE Client_Mute").filters,
            (("role", ("Client_Mute",)),),
        )

    def test_filter_comma_values_are_any_of(self):
        """Comma-separated filter values parse as an any-of tuple."""
        self.assertEqual(
            parse_nodes_args("role router,client").filters,
            (("role", ("router", "client")),),
        )

    def test_filter_dotted_path_keeps_case(self):
        """Dotted paths come from protobuf keys and must not be lowercased."""
        self.assertEqual(
            parse_nodes_args("user.hwModel RAK").filters,
            (("user.hwModel", ("RAK",)),),
        )

    def test_multiple_filters_combine_in_order(self):
        """Distinct field filters accumulate in the order given."""
        query = parse_nodes_args("role router hardware rak limit 3 sort name")
        self.assertEqual(
            query.filters,
            (("role", ("router",)), ("hardware", ("rak",))),
        )
        self.assertEqual(query.limit, 3)
        self.assertEqual(query.sort_field, "name")

    def test_filter_requires_value(self):
        """A trailing filter without a value is a usage error."""
        with self.assertRaises(NodesUsageError):
            parse_nodes_args("role")

    def test_comma_only_filter_value_is_usage_error(self):
        """A filter value of bare commas parses to no values and is rejected."""
        with self.assertRaises(NodesUsageError):
            parse_nodes_args("role ,,")

    def test_unknown_option_is_usage_error(self):
        """Tokens that are neither keywords nor fields raise usage errors."""
        with self.assertRaises(NodesUsageError):
            parse_nodes_args("bogus x")

    def test_sensitive_fields_rejected_everywhere(self):
        """Secret-bearing paths cannot be filtered, sorted, or displayed."""
        for bad in (
            "config.security.psk x",
            "sort config.security.psk",
            "fields config.security.psk",
        ):
            with self.assertRaises(NodesUsageError):
                parse_nodes_args(bad)


def _query_client() -> MagicMock:
    """Provide scrambled node data for query-argument tests."""
    now = datetime.now()
    client = MagicMock()
    client.nodes = {
        "node1": {
            "user": {
                "shortName": "Zed",
                "longName": "Zed Alpha",
                "hwModel": "TBEAM",
                "role": "ROUTER",
            },
            "snr": -8.0,
            "lastHeard": (now - timedelta(hours=2)).timestamp(),
        },
        "node2": {
            "user": {
                "shortName": "Amy",
                "longName": "Amy Brown",
                "hwModel": "RAK4631",
                "role": "CLIENT_MUTE",
            },
            "snr": 12.5,
            "lastHeard": (now - timedelta(minutes=5)).timestamp(),
        },
        "node3": {
            "user": {
                "shortName": "mid",
                "longName": "Mid Carter",
                "hwModel": "RAK4631",
            },
            "snr": 5.0,
            "lastHeard": (now - timedelta(minutes=1)).timestamp(),
            "isFavorite": True,
        },
    }
    return client


def _respond(feature_plugin: Plugin, args: str, client: MagicMock) -> str:
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        return feature_plugin.generate_response(parse_nodes_args(args))


def _generate(feature_plugin: Plugin, client: MagicMock) -> str:
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=client):
        return feature_plugin.generate_response()


def test_role_filter_matches_exact_value_case_insensitively(
    feature_plugin: Plugin,
) -> None:
    response = _respond(feature_plugin, "role client_mute", _query_client())
    assert response.splitlines()[0] == (
        "Nodes: 1 matching (of 3 known) · role ~ client_mute"
    )
    assert "Amy Amy Brown" in response
    assert "Zed" not in response


def test_hardware_filter_matches_substring_case_insensitively(
    feature_plugin: Plugin,
) -> None:
    response = _respond(feature_plugin, "hardware rak", _query_client())
    assert "Nodes: 2 matching (of 3 known) · hardware ~ rak" in response
    assert "Unknown" not in response


def test_name_filter_matches_short_or_long_name(feature_plugin: Plugin) -> None:
    response = _respond(feature_plugin, "name brown", _query_client())
    assert "Nodes: 1 matching (of 3 known) · name ~ brown" in response
    assert "Amy Amy Brown" in response


def test_comma_filter_values_are_any_of(feature_plugin: Plugin) -> None:
    response = _respond(feature_plugin, "role router,client_mute", _query_client())
    assert "Nodes: 2 matching (of 3 known) · role ~ router,client_mute" in response
    assert "Zed Zed Alpha" in response
    assert "Amy Amy Brown" in response
    assert "Mid Carter" not in response


def test_filters_across_fields_combine_with_and(feature_plugin: Plugin) -> None:
    response = _respond(feature_plugin, "hardware rak role client", _query_client())
    assert "Nodes: 1 matching (of 3 known)" in response
    assert "Amy Amy Brown" in response


def test_favorite_filter_matches_boolean_spellings(
    feature_plugin: Plugin,
) -> None:
    client = _query_client()
    response = _respond(feature_plugin, "favorite yes", client)
    assert "Nodes: 1 matching (of 3 known) · favorite ~ yes" in response
    assert "Mid Carter" in response

    client = _query_client()
    response = _respond(feature_plugin, "favorite true", client)
    assert "Mid Carter" in response


def test_public_key_filter_matches_base64_of_bytes(
    feature_plugin: Plugin,
) -> None:
    import base64

    client = _query_client()
    key = bytes.fromhex("deadbeef01")
    client.nodes["node2"]["user"]["publicKey"] = key
    needle = base64.b64encode(key).decode("ascii")[:6]
    response = _respond(feature_plugin, f"public_key {needle}", client)
    assert "Nodes: 1 matching (of 3 known)" in response
    assert "Amy Amy Brown" in response

    client = _query_client()
    client.nodes["node2"]["user"]["publicKey"] = bytearray.fromhex("deadbeef01")
    response = _respond(feature_plugin, f"public_key {needle}", client)
    assert "Amy Amy Brown" in response


def test_power_filter_matches_battery_or_voltage(
    feature_plugin: Plugin,
) -> None:
    client = _query_client()
    client.nodes["node1"]["deviceMetrics"] = {"batteryLevel": 20, "voltage": 4.1}
    client.nodes["node2"]["deviceMetrics"] = {"batteryLevel": 90, "voltage": 3.9}
    response = _respond(feature_plugin, "power 90", client)
    assert "Nodes: 1 matching (of 3 known)" in response
    assert "Amy Amy Brown" in response

    client = _query_client()
    client.nodes["node1"]["deviceMetrics"] = {"batteryLevel": 20, "voltage": 4.1}
    client.nodes["node2"]["deviceMetrics"] = {"batteryLevel": 90, "voltage": 3.9}
    response = _respond(feature_plugin, "power 4.1", client)
    assert "Zed Zed Alpha" in response


def test_battery_sentinels_render_as_powered(feature_plugin: Plugin) -> None:
    client = _query_client()
    client.nodes["node1"]["deviceMetrics"] = {"batteryLevel": 101, "voltage": 4.2}
    client.nodes["node2"]["deviceMetrics"] = {"batteryLevel": 0, "voltage": 3.9}
    response = _respond(feature_plugin, "", client)
    assert "Powered 4.2V" in response
    assert "Powered 3.9V" in response
    assert "101%" not in response
    assert "0%" not in response


def test_battery_field_renders_powered_sentinels(feature_plugin: Plugin) -> None:
    client = _query_client()
    client.nodes["node1"]["deviceMetrics"] = {"batteryLevel": 101}
    response = _respond(feature_plugin, "fields battery", client)
    assert "battery: Powered" in response


def test_no_match_reports_totals(feature_plugin: Plugin) -> None:
    response = _respond(feature_plugin, "role satellite", _query_client())
    assert response == "No nodes matched role ~ satellite (of 3 known)."


def test_default_sort_stays_newest_first(feature_plugin: Plugin) -> None:
    response = _respond(feature_plugin, "", _query_client())
    lines = response.splitlines()
    assert lines[0] == "Nodes: 3"
    assert lines[1].startswith("mid Mid Carter")
    assert lines[2].startswith("Amy Amy Brown")
    assert lines[3].startswith("Zed Zed Alpha")


def test_sort_name_is_case_insensitive_ascending(feature_plugin: Plugin) -> None:
    response = _respond(feature_plugin, "sort name", _query_client())
    lines = response.splitlines()
    assert lines[0] == "Nodes: 3 · sorted by name"
    assert [line.split(" / ")[0] for line in lines[1:]] == [
        "Amy Amy Brown",
        "mid Mid Carter",
        "Zed Zed Alpha",
    ]


def test_sort_numeric_field_defaults_high_to_low(feature_plugin: Plugin) -> None:
    response = _respond(feature_plugin, "sort snr", _query_client())
    lines = response.splitlines()
    assert lines[0] == "Nodes: 3 · sorted by snr"
    assert [line.split(" / ")[3] for line in lines[1:]] == [
        "12.5 dB",
        "5.0 dB",
        "-8.0 dB",
    ]


def test_sort_explicit_direction_overrides_default(feature_plugin: Plugin) -> None:
    response = _respond(feature_plugin, "sort snr asc", _query_client())
    lines = response.splitlines()
    assert lines[0] == "Nodes: 3 · sorted by snr asc"
    assert [line.split(" / ")[3] for line in lines[1:]] == [
        "-8.0 dB",
        "5.0 dB",
        "12.5 dB",
    ]


def test_sort_missing_values_sort_last(feature_plugin: Plugin) -> None:
    response = _respond(feature_plugin, "sort role", _query_client())
    lines = response.splitlines()
    assert lines[-1].startswith("mid Mid Carter")


def test_sort_blank_string_value_sorts_last(feature_plugin: Plugin) -> None:
    client = _query_client()
    client.nodes["node3"]["user"]["hwModel"] = ""
    response = _respond(feature_plugin, "sort hardware", client)
    lines = response.splitlines()
    assert lines[-1].startswith("mid Mid Carter")


def test_sort_name_places_nameless_nodes_last(feature_plugin: Plugin) -> None:
    client = _query_client()
    client.nodes["node3"]["user"] = {}
    response = _respond(feature_plugin, "sort name", client)
    lines = response.splitlines()
    assert lines[-1].startswith("Unknown Unknown")


def test_sort_power_uses_battery_level(feature_plugin: Plugin) -> None:
    client = _query_client()
    client.nodes["node1"]["deviceMetrics"] = {"batteryLevel": 20}
    client.nodes["node2"]["deviceMetrics"] = {"batteryLevel": 90}
    response = _respond(feature_plugin, "sort power", client)
    lines = response.splitlines()
    assert lines[0] == "Nodes: 3 · sorted by power"
    assert lines[1].startswith("Amy Amy Brown")
    assert lines[2].startswith("Zed Zed Alpha")


def test_sort_survives_non_numeric_value_in_numeric_field(
    feature_plugin: Plugin,
) -> None:
    client = _query_client()
    client.nodes["node3"]["snr"] = "garbage"
    response = _respond(feature_plugin, "sort snr", client)
    assert "Nodes: 3 · sorted by snr" in response
    assert response.count("\n") >= 3


def _many_nodes_client(count: int) -> MagicMock:
    now = datetime.now()
    client = MagicMock()
    client.nodes = {
        f"node{i}": {
            "user": {
                "shortName": f"N{i:02d}",
                "longName": f"Node {i:02d}",
                "hwModel": "RAK4631",
                "role": "CLIENT",
            },
            "snr": float(i % 10),
            "lastHeard": (now - timedelta(minutes=count - i)).timestamp(),
        }
        for i in range(count)
    }
    return client


def test_default_limit_is_twenty(feature_plugin: Plugin) -> None:
    response = _generate(feature_plugin, _many_nodes_client(25))
    assert response.splitlines()[0] == "Nodes: 20 of 25"
    assert response.splitlines()[-1] == "… and 5 more not shown"
    assert "Node 24" in response
    assert "Node 04" not in response


def test_limit_argument_overrides_default(feature_plugin: Plugin) -> None:
    response = _respond(feature_plugin, "limit 2", _many_nodes_client(25))
    assert response.splitlines()[0] == "Nodes: 2 of 25"
    assert response.splitlines()[-1] == "… and 23 more not shown"


def test_limit_all_and_bare_zero_list_everything(feature_plugin: Plugin) -> None:
    for args in ("limit all", "0"):
        response = _respond(feature_plugin, args, _many_nodes_client(25))
        assert response.splitlines()[0] == "Nodes: 25"
        assert "more not shown" not in response


def test_configured_max_results_applies(feature_plugin: Plugin) -> None:
    feature_plugin.config["max_results"] = 2
    response = _generate(feature_plugin, _many_nodes_client(25))
    assert response.splitlines()[0] == "Nodes: 2 of 25"


def test_invalid_configured_max_results_falls_back_to_default(
    feature_plugin: Plugin,
) -> None:
    for invalid in ("many", -1, True):
        feature_plugin.config["max_results"] = invalid
        response = _generate(feature_plugin, _many_nodes_client(25))
        assert response.splitlines()[0] == (f"Nodes: {DEFAULT_MAX_RESULTS} of 25")
    assert feature_plugin.logger.warning.called


def test_filter_and_limit_combine(feature_plugin: Plugin) -> None:
    client = _many_nodes_client(25)
    client.nodes["node0"]["user"]["role"] = "ROUTER"
    client.nodes["node1"]["user"]["role"] = "ROUTER"
    client.nodes["node2"]["user"]["role"] = "ROUTER"
    response = _respond(feature_plugin, "role router limit 2", client)
    assert response.splitlines()[0] == (
        "Nodes: 2 of 3 matching (of 25 known) · role ~ router"
    )
    assert response.splitlines()[-1] == "… and 1 more not shown"


def test_display_fields_override_lists_only_those_fields(
    feature_plugin: Plugin,
) -> None:
    client = _query_client()
    client.nodes["node1"]["deviceMetrics"] = {"batteryLevel": 85, "voltage": 4.2}
    response = _respond(feature_plugin, "fields battery,voltage", client)
    lines = response.splitlines()
    assert lines[0] == "Nodes: 3"
    assert "battery: 85% / voltage: 4.2V" in response
    assert "TBEAM" not in response
    assert "RAK4631" not in response


def test_dotted_path_display_override(feature_plugin: Plugin) -> None:
    client = _query_client()
    client.nodes["node1"]["environmentMetrics"] = {"temperature": 21.5}
    response = _respond(feature_plugin, "fields environmentMetrics.temperature", client)
    assert "environmentMetrics.temperature: 21.5" in response


def test_handle_room_message_help_replies_usage(feature_plugin: Plugin) -> None:
    feature_plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("nodes", "HELP")
    )
    room = MagicMock()
    room.room_id = "!test:matrix.org"
    event = MagicMock()

    async def run_test() -> None:
        result = await feature_plugin.handle_room_message(room, event, "!nodes help")
        assert result is True
        call_args = feature_plugin.send_matrix_message.call_args
        assert call_args.kwargs["message"] == USAGE_TEXT
        assert call_args.kwargs["formatted"] is False
        feature_plugin.send_matrix_reaction.assert_called_once_with(
            "!test:matrix.org", event.event_id, "✅"
        )

    asyncio.run(run_test())


def test_handle_room_message_usage_error_replies_usage(
    feature_plugin: Plugin,
) -> None:
    feature_plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("nodes", "bogus")
    )
    room = MagicMock()
    room.room_id = "!test:matrix.org"
    event = MagicMock()

    async def run_test() -> None:
        result = await feature_plugin.handle_room_message(room, event, "!nodes bogus")
        assert result is True
        call_args = feature_plugin.send_matrix_message.call_args
        assert "Unknown option or field 'bogus'" in call_args.kwargs["message"]
        assert USAGE_TEXT in call_args.kwargs["message"]
        feature_plugin.send_matrix_reaction.assert_called_once_with(
            "!test:matrix.org", event.event_id, "❌"
        )

    asyncio.run(run_test())


@patch("mmrelay.meshtastic_utils.connect_meshtastic")
def test_handle_room_message_passes_args_to_response(
    mock_connect: MagicMock, feature_plugin: Plugin
) -> None:
    mock_connect.return_value = _query_client()
    feature_plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("nodes", "role client_mute")
    )
    room = MagicMock()
    room.room_id = "!test:matrix.org"
    event = MagicMock()

    async def run_test() -> None:
        result = await feature_plugin.handle_room_message(
            room, event, "!nodes role client_mute"
        )
        assert result is True
        message = feature_plugin.send_matrix_message.call_args.kwargs["message"]
        assert "Nodes: 1 matching (of 3 known)" in message
        assert "Amy Amy Brown" in message

    asyncio.run(run_test())


@pytest.mark.parametrize(
    "args",
    ["²", "limit ²", "9" * 5000, "limit " + "9" * 5000],
    ids=[
        "unicode-digit",
        "unicode-limit",
        "overlong-digit",
        "overlong-limit",
    ],
)
def test_malformed_queries_raise_usage_errors(args: str) -> None:
    with pytest.raises(NodesUsageError):
        parse_nodes_args(args)


@pytest.mark.parametrize(
    "direction, expected",
    [
        ("", ["Amy", "Zed", "mid"]),
        ("asc", ["Zed", "Amy", "mid"]),
        ("desc", ["Amy", "Zed", "mid"]),
    ],
)
def test_numeric_dotted_sort_defaults_to_descending(
    feature_plugin: Plugin, direction: str, expected: list[str]
) -> None:
    client = _query_client()
    client.nodes["node1"]["environmentMetrics"] = {"temperature": 20.0}
    client.nodes["node2"]["environmentMetrics"] = {"temperature": 30.0}
    response = _respond(
        feature_plugin, f"sort environmentMetrics.temperature {direction}", client
    )
    assert [line.split()[0] for line in response.splitlines()[1:]] == expected


@pytest.mark.parametrize("invalid", ["garbage", "NaN", float("inf"), True])
@pytest.mark.parametrize(
    "direction, expected",
    [("asc", ["Zed", "Amy", "mid"]), ("desc", ["Amy", "Zed", "mid"])],
)
def test_invalid_numeric_sort_values_are_last(
    feature_plugin: Plugin, invalid: Any, direction: str, expected: list[str]
) -> None:
    client = _query_client()
    client.nodes["node3"]["snr"] = invalid
    response = _respond(feature_plugin, f"sort snr {direction}", client)
    assert [line.split()[0] for line in response.splitlines()[1:]] == expected


def test_canonical_top_level_fields_keep_their_case(feature_plugin: Plugin) -> None:
    client = _query_client()
    client.nodes["node1"]["hopsAway"] = 2
    response = _respond(feature_plugin, "fields hopsAway,lastHeard", client)
    assert "hopsAway: 2" in response
    assert "lastHeard: " + str(client.nodes["node1"]["lastHeard"]) in response


def test_canonical_top_level_fields_support_filter_and_sort(
    feature_plugin: Plugin,
) -> None:
    client = _query_client()
    client.nodes["node1"]["hopsAway"] = 2
    client.nodes["node2"]["hopsAway"] = 1
    client.nodes["node3"]["hopsAway"] = 2
    response = _respond(feature_plugin, "hopsAway 2 sort lastHeard", client)
    assert "Nodes: 2 matching (of 3 known)" in response
    assert [line.split()[0] for line in response.splitlines()[1:]] == ["mid", "Zed"]


@pytest.mark.parametrize(
    "args", ["²", "limit " + "9" * 5000], ids=["unicode-digit", "overlong-limit"]
)
def test_malformed_numeric_limit_gets_a_usage_reply(
    feature_plugin: Plugin, args: str
) -> None:
    feature_plugin.get_matching_matrix_command_with_args = MagicMock(
        return_value=("nodes", args)
    )
    room = MagicMock(room_id="!test:matrix.org")
    event = MagicMock(event_id="$query")
    with patch("mmrelay.meshtastic_utils.connect_meshtastic") as connect:
        assert (
            asyncio.run(
                feature_plugin.handle_room_message(room, event, "!nodes " + args)
            )
            is True
        )
    connect.assert_not_called()
    assert USAGE_TEXT in feature_plugin.send_matrix_message.call_args.kwargs["message"]
    feature_plugin.send_matrix_reaction.assert_called_once_with(
        room.room_id, event.event_id, "❌"
    )


@pytest.mark.parametrize(
    "direction, expected",
    [
        ("", ["Amy", "Zed", "mid"]),
        ("asc", ["Zed", "Amy", "mid"]),
        ("desc", ["Amy", "Zed", "mid"]),
    ],
)
def test_inferred_numeric_sort_keeps_invalid_values_last(
    feature_plugin: Plugin, direction: str, expected: list[str]
) -> None:
    client = _query_client()
    client.nodes["node1"]["environmentMetrics"] = {"temperature": 9.0}
    client.nodes["node2"]["environmentMetrics"] = {"temperature": "10"}
    client.nodes["node3"]["environmentMetrics"] = {"temperature": "garbage"}
    response = _respond(
        feature_plugin, f"sort environmentMetrics.temperature {direction}", client
    )
    assert [line.split()[0] for line in response.splitlines()[1:]] == expected


if __name__ == "__main__":
    unittest.main()
