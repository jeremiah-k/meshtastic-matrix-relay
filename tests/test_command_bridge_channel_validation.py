#!/usr/bin/env python3
"""Tests for meshtastic_channel validation in _get_meshtastic_interface_and_channel."""

import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.matrix_utils import _get_meshtastic_interface_and_channel


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestMeshtasticChannelValidation(unittest.TestCase):
    """Ensure booleans are rejected and valid integers are accepted."""

    @patch("mmrelay.matrix_utils.connect_meshtastic")
    def test_boolean_true_rejected(self, mock_connect):
        mock_connect.return_value = MagicMock()
        result_iface, result_ch = _run(
            _get_meshtastic_interface_and_channel({"meshtastic_channel": True}, "test")
        )
        self.assertIsNone(result_iface)
        self.assertIsNone(result_ch)

    @patch("mmrelay.matrix_utils.connect_meshtastic")
    def test_boolean_false_rejected(self, mock_connect):
        mock_connect.return_value = MagicMock()
        result_iface, result_ch = _run(
            _get_meshtastic_interface_and_channel({"meshtastic_channel": False}, "test")
        )
        self.assertIsNone(result_iface)
        self.assertIsNone(result_ch)

    @patch("mmrelay.matrix_utils.connect_meshtastic")
    def test_integer_zero_accepted(self, mock_connect):
        mock_iface = MagicMock()
        mock_connect.return_value = mock_iface
        result_iface, result_ch = _run(
            _get_meshtastic_interface_and_channel({"meshtastic_channel": 0}, "test")
        )
        self.assertIs(result_iface, mock_iface)
        self.assertEqual(result_ch, 0)

    @patch("mmrelay.matrix_utils.connect_meshtastic")
    def test_positive_integer_accepted(self, mock_connect):
        mock_iface = MagicMock()
        mock_connect.return_value = mock_iface
        result_iface, result_ch = _run(
            _get_meshtastic_interface_and_channel({"meshtastic_channel": 3}, "test")
        )
        self.assertIs(result_iface, mock_iface)
        self.assertEqual(result_ch, 3)


if __name__ == "__main__":
    unittest.main()
