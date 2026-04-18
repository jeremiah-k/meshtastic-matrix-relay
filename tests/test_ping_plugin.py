#!/usr/bin/env python3
"""
Test suite for the MMRelay ping plugin.

Tests the ping/pong functionality including:
- Case matching utility function
- Explicit !ping command (default behavior)
- mimic_mode for conversational matching of bare "ping"
- Direct message vs broadcast handling
- Response delay and message routing
- Matrix room message handling
- Channel enablement checking
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from meshtastic.mesh_interface import BROADCAST_NUM

from mmrelay.constants.formats import DEFAULT_CHANNEL, TEXT_MESSAGE_APP
from mmrelay.constants.messages import PING_RESPONSE
from mmrelay.plugins.ping_plugin import Plugin, match_case


class TestMatchCase(unittest.TestCase):
    def test_match_case_all_lowercase(self):
        result = match_case("ping", "pong")
        self.assertEqual(result, "pong")

    def test_match_case_all_uppercase(self):
        result = match_case("PING", "pong")
        self.assertEqual(result, "PONG")

    def test_match_case_mixed_case(self):
        result = match_case("PiNg", "pong")
        self.assertEqual(result, "PoNg")

    def test_match_case_first_letter_uppercase(self):
        result = match_case("Ping", "pong")
        self.assertEqual(result, "Pong")

    def test_match_case_different_lengths(self):
        result = match_case("Pi", "pong")
        self.assertEqual(result, "Po")

    def test_match_case_empty_strings(self):
        result = match_case("", "pong")
        self.assertEqual(result, "")


class TestPingPlugin(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()
        self.plugin.is_channel_enabled = MagicMock(return_value=True)
        self.plugin.get_response_delay = MagicMock(return_value=1.0)
        self.plugin.send_matrix_message = AsyncMock()
        self.plugin.send_message = MagicMock()

    def test_plugin_name(self):
        self.assertEqual(self.plugin.plugin_name, "ping")

    def test_description_property(self):
        self.assertEqual(
            self.plugin.description,
            "Check connectivity with the relay; optional mimic mode responds to mesh pings",
        )

    def test_get_matrix_commands(self):
        self.assertEqual(self.plugin.get_matrix_commands(), ["ping"])

    def test_get_mesh_commands(self):
        self.assertEqual(self.plugin.get_mesh_commands(), ["ping"])

    def test_get_mimic_mode_default(self):
        self.assertFalse(self.plugin.get_mimic_mode())

    def test_get_mimic_mode_true(self):
        self.plugin.config = {"mimic_mode": True}
        self.assertTrue(self.plugin.get_mimic_mode())

    def test_get_mimic_mode_false(self):
        self.plugin.config = {"mimic_mode": False}
        self.assertFalse(self.plugin.get_mimic_mode())

    def test_get_mimic_mode_non_boolean_disabled(self):
        for mimic_mode_value in ("false", "true", 1):
            with self.subTest(mimic_mode_value=mimic_mode_value):
                self.plugin.config = {"mimic_mode": mimic_mode_value}
                self.assertFalse(self.plugin.get_mimic_mode())
        self.plugin.logger.warning.assert_called_once_with(
            "Invalid ping.mimic_mode value %r; expected boolean. Defaulting to false.",
            "false",
        )
        self.assertTrue(self.plugin._invalid_mimic_mode_warned)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_handle_meshtastic_message_missing_myinfo(self, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo = None
        mock_client.nodes = {}
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            self.plugin.logger.warning.assert_called_once_with(
                "Meshtastic client myInfo unavailable; skipping ping"
            )
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_explicit_ping_broadcast(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
            "id": 123,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            self.plugin.is_channel_enabled.assert_called_once_with(
                0, is_direct_message=False
            )
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text=PING_RESPONSE, channel=0, reply_id=123
            )
            self.plugin.logger.info.assert_called_once()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_explicit_ping_direct_message(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!ping"},
            "channel": 1,
            "fromId": "!12345678",
            "to": 123456789,
            "id": 123,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            self.plugin.is_channel_enabled.assert_called_once_with(
                1, is_direct_message=True
            )
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text=PING_RESPONSE,
                channel=1,
                destination_id="!12345678",
                reply_id=123,
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_explicit_ping_case_insensitive_normalized_response(
        self, mock_sleep, mock_connect
    ):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!PING"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text=PING_RESPONSE, channel=0, reply_id=None
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_bare_ping_ignored_by_default(self, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_ping_in_sentence_ignored_by_default(self, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "how far does the ping go?"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_explicit_ping_in_prose_ignored_by_default(self, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "please !ping now"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_double_bang_ping_ignored_by_default(self, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!!ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_explicit_ping_with_trailing_punctuation_ignored_by_default(
        self, mock_connect
    ):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!ping?"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_bare_ping_ignored_mimic_mode_false(self, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client
        self.plugin.config = {"mimic_mode": False}

        packet = {
            "decoded": {"text": "ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_explicit_ping_works_with_mimic_mode_false(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client
        self.plugin.config = {"mimic_mode": False}

        packet = {
            "decoded": {"text": "!ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text=PING_RESPONSE, channel=0, reply_id=None
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_no_ping_no_response(self, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "Hello world"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_channel_disabled(self, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client
        self.plugin.is_channel_enabled = MagicMock(return_value=False)

        packet = {
            "decoded": {"text": "!ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    def test_match_case_empty_source(self):
        """Empty source returns empty string (line 40)."""
        self.assertEqual(match_case("", "pong"), "")

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_handle_meshtastic_message_non_text_portnum(self, mock_connect):
        """Non-text portnum should be rejected (line 99)."""
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"portnum": "TELEMETRY_APP", "text": "!ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)

        asyncio.run(run_test())

    def test_get_matrix_commands_none_name(self):
        """get_matrix_commands returns [] when plugin_name is None (line 192)."""
        self.plugin.plugin_name = None
        self.assertEqual(self.plugin.get_matrix_commands(), [])

    def test_get_mesh_commands_none_name(self):
        """get_mesh_commands returns [] when plugin_name is None (line 203)."""
        self.plugin.plugin_name = None
        self.assertEqual(self.plugin.get_mesh_commands(), [])

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_handle_meshtastic_message_no_client(self, mock_connect):
        """Missing meshtastic client should return True (line 128-129)."""
        mock_connect.return_value = None

        packet = {
            "decoded": {"text": "!ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            self.plugin.logger.warning.assert_called_once_with(
                "Meshtastic client unavailable; skipping ping"
            )

        asyncio.run(run_test())


class TestPingPluginMimicMode(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()
        self.plugin.is_channel_enabled = MagicMock(return_value=True)
        self.plugin.get_response_delay = MagicMock(return_value=1.0)
        self.plugin.config = {"mimic_mode": True}
        self.plugin.send_message = MagicMock()

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_bare_ping(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text="pong", channel=0, reply_id=None
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_mode_explicit_ping_uses_mimic_response(
        self, mock_sleep, mock_connect
    ):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        async def run_test() -> None:
            for message, expected_response in (("!ping", "!pong"), ("!PING", "!PONG")):
                with self.subTest(message=message, expected_response=expected_response):
                    packet = {
                        "decoded": {"text": message},
                        "channel": 0,
                        "fromId": "!12345678",
                        "to": BROADCAST_NUM,
                    }
                    result = await self.plugin.handle_meshtastic_message(
                        packet, "formatted_message", "TestNode", "TestMesh"
                    )
                    self.assertTrue(result)
                    mock_sleep.assert_called_once_with(1.0)
                    self.plugin.send_message.assert_called_once_with(
                        text=expected_response, channel=0, reply_id=None
                    )
                    mock_sleep.reset_mock()
                    self.plugin.send_message.reset_mock()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_case_matching_upper(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "PING"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text="PONG", channel=0, reply_id=None
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_case_matching_title(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "Ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text="Pong", channel=0, reply_id=None
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_punctuation_preserved(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!ping!"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text="!pong!", channel=0, reply_id=None
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_balanced_wrapper_punctuation_preserved(
        self, mock_sleep, mock_connect
    ):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!!!ping!!!"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text="!!!pong!!!", channel=0, reply_id=None
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_one_side_at_limit_preserved(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!!!!!ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text="!!!!!pong", channel=0, reply_id=None
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_both_sides_at_limit_preserved(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!!!!!Ping?????"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text="!!!!!Pong?????", channel=0, reply_id=None
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_case_and_punctuation_mixed(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "PiNg!?!"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text="PoNg!?!", channel=0, reply_id=None
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_mimic_ping_in_sentence_ignored(self, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "how far does the ping go?"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_mimic_ping_prose_variants_ignored(self, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        async def run_test() -> None:
            for message in (
                "please ping",
                "can you ping?",
                "ping me",
                "before ping after",
            ):
                with self.subTest(message=message):
                    packet = {
                        "decoded": {"text": message},
                        "channel": 0,
                        "fromId": "!12345678",
                        "to": BROADCAST_NUM,
                    }
                    result = await self.plugin.handle_meshtastic_message(
                        packet, "formatted_message", "TestNode", "TestMesh"
                    )
                    self.assertFalse(result)
                    self.plugin.send_message.assert_not_called()
                    self.plugin.send_message.reset_mock()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_mimic_whitespace_only_ignored(self, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "     "},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_first_word_ping_with_trailing_text_ignored(
        self, mock_sleep, mock_connect
    ):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "ping now"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            mock_sleep.assert_not_called()
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_first_word_wrapped_ping_with_trailing_text_ignored(
        self, mock_sleep, mock_connect
    ):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!!!!!PInG?!!!! status"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            mock_sleep.assert_not_called()
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_one_side_exceeds_max_uses_fallback(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!!!!!!PING?"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text="Pong...", channel=0, reply_id=None
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_post_side_exceeds_max_uses_fallback(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "??pIng!!!!!!"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text="Pong...", channel=0, reply_id=None
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_direct_message(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "ping"},
            "channel": 1,
            "fromId": "!12345678",
            "to": 123456789,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            self.plugin.is_channel_enabled.assert_called_once_with(
                1, is_direct_message=True
            )
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text="pong", channel=1, destination_id="!12345678", reply_id=None
            )

        asyncio.run(run_test())


class TestPingPluginMatrixHandling(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()
        self.plugin.send_matrix_message = AsyncMock()
        self.plugin.send_matrix_reaction = AsyncMock()

    def test_handle_room_message_no_match(self):
        self.plugin.matches = MagicMock(return_value=False)
        room = MagicMock()
        event = MagicMock()

        async def run_test() -> None:
            result = await self.plugin.handle_room_message(room, event, "full_message")
            self.assertFalse(result)
            self.plugin.matches.assert_called_once_with(event)
            self.plugin.send_matrix_message.assert_not_called()

        asyncio.run(run_test())

    def test_handle_room_message_ping_match(self):
        self.plugin.matches = MagicMock(return_value=True)
        room = MagicMock()
        room.room_id = "!test:matrix.org"
        event = MagicMock()

        async def run_test() -> None:
            result = await self.plugin.handle_room_message(room, event, "bot: !ping")
            self.assertTrue(result)
            self.plugin.matches.assert_called_once_with(event)
            self.plugin.send_matrix_reaction.assert_called_once_with(
                "!test:matrix.org", event.event_id, "✅"
            )
            self.plugin.send_matrix_message.assert_called_once_with(
                "!test:matrix.org", PING_RESPONSE
            )

        asyncio.run(run_test())


class TestPingPluginEdgeCases(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()
        self.plugin.is_channel_enabled = MagicMock(return_value=True)
        self.plugin.get_response_delay = MagicMock(return_value=1.0)
        self.plugin.send_message = MagicMock()

    def test_handle_meshtastic_message_no_decoded(self):
        packet = {"channel": 0, "fromId": "!12345678", "to": BROADCAST_NUM}

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)

        asyncio.run(run_test())

    def test_handle_meshtastic_message_no_text(self):
        packet = {
            "decoded": {"portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_broadcast_num_explicit(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"portnum": TEXT_MESSAGE_APP, "text": "!ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text=PING_RESPONSE, channel=0, reply_id=None
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_direct_message_missing_fromId(self, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!ping"},
            "channel": 1,
            "to": 123456789,
        }

        async def run_test() -> None:
            with patch("asyncio.sleep") as mock_sleep:
                result = await self.plugin.handle_meshtastic_message(
                    packet, "formatted_message", "TestNode", "TestMesh"
                )
                self.assertTrue(result)
                mock_sleep.assert_not_called()
                self.plugin.send_message.assert_not_called()
                self.plugin.logger.warning.assert_called_once_with(
                    "Direct message missing fromId; cannot reply"
                )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_packet_targeted_to_other_node_ignored(self, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!ping"},
            "channel": 1,
            "fromId": "!12345678",
            "to": 987654321,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            self.plugin.is_channel_enabled.assert_not_called()
            self.plugin.send_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_channel_none_coalesces_to_default_channel(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!ping"},
            "channel": None,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test() -> None:
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            self.plugin.is_channel_enabled.assert_called_once_with(
                DEFAULT_CHANNEL, is_direct_message=False
            )
            mock_sleep.assert_called_once_with(1.0)
            self.plugin.send_message.assert_called_once_with(
                text=PING_RESPONSE,
                channel=DEFAULT_CHANNEL,
                reply_id=None,
            )

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
