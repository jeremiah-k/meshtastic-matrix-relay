"""Tests for meshtastic_channel validation in _get_meshtastic_interface_and_channel."""

from unittest.mock import MagicMock, patch

import pytest

from mmrelay.matrix_utils import _get_meshtastic_interface_and_channel

pytestmark = pytest.mark.asyncio


@patch("mmrelay.matrix_utils._connect_meshtastic")
async def test_boolean_true_rejected(mock_connect):
    mock_connect.return_value = MagicMock()
    result_iface, result_ch = await _get_meshtastic_interface_and_channel(
        {"meshtastic_channel": True}, "test"
    )
    assert result_iface is None
    assert result_ch is None
    mock_connect.assert_not_called()


@patch("mmrelay.matrix_utils._connect_meshtastic")
async def test_boolean_false_rejected(mock_connect):
    mock_connect.return_value = MagicMock()
    result_iface, result_ch = await _get_meshtastic_interface_and_channel(
        {"meshtastic_channel": False}, "test"
    )
    assert result_iface is None
    assert result_ch is None
    mock_connect.assert_not_called()


@patch("mmrelay.matrix_utils._connect_meshtastic")
async def test_missing_channel_rejected_before_connect(mock_connect):
    mock_connect.return_value = MagicMock()
    result_iface, result_ch = await _get_meshtastic_interface_and_channel({}, "test")
    assert result_iface is None
    assert result_ch is None
    mock_connect.assert_not_called()


@patch("mmrelay.matrix_utils._connect_meshtastic")
async def test_integer_zero_accepted(mock_connect):
    mock_iface = MagicMock()
    mock_connect.return_value = mock_iface
    result_iface, result_ch = await _get_meshtastic_interface_and_channel(
        {"meshtastic_channel": 0}, "test"
    )
    assert result_iface is mock_iface
    assert result_ch == 0
    mock_connect.assert_called_once()


@patch("mmrelay.matrix_utils._connect_meshtastic")
async def test_positive_integer_accepted(mock_connect):
    mock_iface = MagicMock()
    mock_connect.return_value = mock_iface
    result_iface, result_ch = await _get_meshtastic_interface_and_channel(
        {"meshtastic_channel": 3}, "test"
    )
    assert result_iface is mock_iface
    assert result_ch == 3
    mock_connect.assert_called_once()
