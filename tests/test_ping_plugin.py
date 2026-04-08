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

from mmrelay.constants.formats import TEXT_MESSAGE_APP
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
        self.plugin.config = {"mimic_mode": "false"}
        self.assertFalse(self.plugin.get_mimic_mode())

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            self.plugin.logger.warning.assert_called_once_with(
                "Meshtastic client myInfo unavailable; skipping ping"
            )
            mock_client.sendText.assert_not_called()

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
        }

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            self.plugin.is_channel_enabled.assert_called_once_with(
                0, is_direct_message=False
            )
            mock_sleep.assert_called_once_with(1.0)
            mock_client.sendText.assert_called_once_with(text="pong", channelIndex=0)
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
        }

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            self.plugin.is_channel_enabled.assert_called_once_with(
                1, is_direct_message=True
            )
            mock_client.sendText.assert_called_once_with(
                text="pong", destinationId="!12345678"
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_explicit_ping_case_matching(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!PING"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_client.sendText.assert_called_once_with(text="PONG", channelIndex=0)

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            mock_client.sendText.assert_not_called()

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            mock_client.sendText.assert_not_called()

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            mock_client.sendText.assert_not_called()

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            mock_client.sendText.assert_not_called()

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            mock_client.sendText.assert_not_called()

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            mock_client.sendText.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_explicit_ping_works_with_mimic_mode_false(self, mock_connect):
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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_client.sendText.assert_called_once_with(text="pong", channelIndex=0)

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            mock_client.sendText.assert_not_called()

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)
            mock_client.sendText.assert_not_called()

        asyncio.run(run_test())


class TestPingPluginMimicMode(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()
        self.plugin.is_channel_enabled = MagicMock(return_value=True)
        self.plugin.get_response_delay = MagicMock(return_value=1.0)
        self.plugin.config = {"mimic_mode": True}

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_client.sendText.assert_called_once_with(text="pong", channelIndex=0)

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_client.sendText.assert_called_once_with(text="PONG", channelIndex=0)

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_client.sendText.assert_called_once_with(text="Pong", channelIndex=0)

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_client.sendText.assert_called_once_with(text="!pong!", channelIndex=0)

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_excessive_punctuation_fallback(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!!!ping!!!"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_client.sendText.assert_called_once_with(text="Pong...", channelIndex=0)

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_mimic_ping_in_sentence(self, mock_sleep, mock_connect):
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "how far does the ping go?"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_client.sendText.assert_called_once_with(text="pong", channelIndex=0)

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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            self.plugin.is_channel_enabled.assert_called_once_with(
                1, is_direct_message=True
            )
            mock_client.sendText.assert_called_once_with(
                text="pong", destinationId="!12345678"
            )

        asyncio.run(run_test())


class TestPingPluginMatrixHandling(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()
        self.plugin.send_matrix_message = AsyncMock()

    def test_handle_room_message_no_match(self):
        self.plugin.matches = MagicMock(return_value=False)
        room = MagicMock()
        event = MagicMock()

        async def run_test():
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

        async def run_test():
            result = await self.plugin.handle_room_message(room, event, "bot: !ping")
            self.assertTrue(result)
            self.plugin.matches.assert_called_once_with(event)
            self.plugin.send_matrix_message.assert_called_once_with(
                "!test:matrix.org", "pong!"
            )

        asyncio.run(run_test())


class TestPingPluginEdgeCases(unittest.TestCase):
    def setUp(self):
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()
        self.plugin.is_channel_enabled = MagicMock(return_value=True)
        self.plugin.get_response_delay = MagicMock(return_value=1.0)

    def test_handle_meshtastic_message_no_decoded(self):
        packet = {"channel": 0, "fromId": "!12345678", "to": BROADCAST_NUM}

        async def run_test():
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

        async def run_test():
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

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)
            mock_client.sendText.assert_called_once_with(text="pong", channelIndex=0)

        asyncio.run(run_test())
        mock_sleep.assert_called()


if __name__ == "__main__":
    unittest.main()
