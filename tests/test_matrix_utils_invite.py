#!/usr/bin/env python3
"""
Test suite for Matrix room invitation handling in MMRelay.

Tests automatic room joining on invitation:
- _is_room_mapped() helper function
- on_invite() callback function
- Room filtering and validation
- Edge cases for various invite scenarios
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from mmrelay.matrix_utils import (
    _is_room_mapped,
    on_invite,
)


@pytest.mark.usefixtures("reset_matrix_utils_globals")
def test_is_room_mapped_with_list_format_room_id() -> None:
    """
    Test that _is_room_mapped returns True for a room ID in list format.
    """
    mapping = [
        {"id": "!abc123:matrix.org", "meshtastic_channel": 0},
        {"id": "!def456:matrix.org", "meshtastic_channel": 1},
    ]

    result = _is_room_mapped(mapping, "!abc123:matrix.org")
    assert result is True


@pytest.mark.usefixtures("reset_matrix_utils_globals")
def test_is_room_mapped_with_list_format_alias() -> None:
    """
    Test that _is_room_mapped returns True for a room alias in list format.
    """
    mapping = [
        {"id": "#general:matrix.org", "meshtastic_channel": 0},
        {"id": "#random:matrix.org", "meshtastic_channel": 1},
    ]

    result = _is_room_mapped(mapping, "#general:matrix.org")
    assert result is True


@pytest.mark.usefixtures("reset_matrix_utils_globals")
def test_is_room_mapped_with_dict_format_room_id() -> None:
    """
    Test that _is_room_mapped returns True for a room ID in dict format.
    """
    mapping = {
        "general": {"id": "!abc123:matrix.org", "meshtastic_channel": 0},
        "random": {"id": "!def456:matrix.org", "meshtastic_channel": 1},
    }

    result = _is_room_mapped(mapping, "!abc123:matrix.org")
    assert result is True


@pytest.mark.usefixtures("reset_matrix_utils_globals")
def test_is_room_mapped_with_dict_format_alias() -> None:
    """
    Test that _is_room_mapped returns True for a room alias in dict format.
    """
    mapping = {
        "general": {"id": "#general:matrix.org", "meshtastic_channel": 0},
        "random": {"id": "#random:matrix.org", "meshtastic_channel": 1},
    }

    result = _is_room_mapped(mapping, "#general:matrix.org")
    assert result is True


@pytest.mark.usefixtures("reset_matrix_utils_globals")
def test_is_room_mapped_not_found() -> None:
    """
    Test that _is_room_mapped returns False for unmapped rooms.
    """
    mapping = [
        {"id": "!abc123:matrix.org", "meshtastic_channel": 0},
        {"id": "!def456:matrix.org", "meshtastic_channel": 1},
    ]

    result = _is_room_mapped(mapping, "!xyz789:matrix.org")
    assert result is False


@pytest.mark.usefixtures("reset_matrix_utils_globals")
def test_is_room_mapped_with_empty_mapping() -> None:
    """
    Test that _is_room_mapped returns False for empty mapping.
    """
    mapping = []
    result = _is_room_mapped(mapping, "!abc123:matrix.org")
    assert result is False


@pytest.mark.usefixtures("reset_matrix_utils_globals")
def test_is_room_mapped_with_none_mapping() -> None:
    """
    Test that _is_room_mapped returns False for None mapping.
    """
    result = _is_room_mapped(None, "!abc123:matrix.org")
    assert result is False


@pytest.mark.usefixtures("reset_matrix_utils_globals")
def test_is_room_mapped_with_invalid_type() -> None:
    """
    Test that _is_room_mapped returns False for invalid mapping types.
    """
    result = _is_room_mapped("invalid", "!abc123:matrix.org")
    assert result is False

    result = _is_room_mapped(123, "!abc123:matrix.org")
    assert result is False


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@patch("mmrelay.matrix_utils.logger")
async def test_on_invite_ignores_non_bot_invites(mock_logger: MagicMock) -> None:
    """
    Test that on_invite ignores invites not directed at the bot.
    """
    mock_room = MagicMock()
    mock_room.room_id = "!abc123:matrix.org"

    mock_event = MagicMock()
    mock_event.state_key = "@other:matrix.org"
    mock_event.membership = "invite"
    mock_event.sender = "@inviter:matrix.org"

    import mmrelay.matrix_utils

    mmrelay.matrix_utils.bot_user_id = "@bot:matrix.org"
    mmrelay.matrix_utils.matrix_rooms = [
        {"id": "!abc123:matrix.org", "meshtastic_channel": 0}
    ]

    result = await on_invite(mock_room, mock_event)

    assert result is None
    mock_logger.debug.assert_any_call(
        "Ignoring invite for @other:matrix.org (not for bot @bot:matrix.org)"
    )


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@patch("mmrelay.matrix_utils.logger")
async def test_on_invite_ignores_non_invite_membership(
    mock_logger: MagicMock,
) -> None:
    """
    Test that on_invite ignores non-invite membership events.
    """
    mock_room = MagicMock()
    mock_room.room_id = "!abc123:matrix.org"

    mock_event = MagicMock()
    mock_event.state_key = "@bot:matrix.org"
    mock_event.membership = "join"
    mock_event.sender = "@inviter:matrix.org"

    import mmrelay.matrix_utils

    mmrelay.matrix_utils.bot_user_id = "@bot:matrix.org"
    mmrelay.matrix_utils.matrix_rooms = [
        {"id": "!abc123:matrix.org", "meshtastic_channel": 0}
    ]

    result = await on_invite(mock_room, mock_event)

    assert result is None
    mock_logger.debug.assert_any_call("Ignoring non-invite membership event: join")


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@patch("mmrelay.matrix_utils.logger")
async def test_on_invite_ignores_unmapped_rooms(mock_logger: MagicMock) -> None:
    """
    Test that on_invite ignores invites to unmapped rooms.
    """
    mock_room = MagicMock()
    mock_room.room_id = "!abc123:matrix.org"

    mock_event = MagicMock()
    mock_event.state_key = "@bot:matrix.org"
    mock_event.membership = "invite"
    mock_event.sender = "@inviter:matrix.org"

    import mmrelay.matrix_utils

    mmrelay.matrix_utils.bot_user_id = "@bot:matrix.org"
    mmrelay.matrix_utils.matrix_rooms = [
        {"id": "!other:matrix.org", "meshtastic_channel": 0}
    ]

    result = await on_invite(mock_room, mock_event)

    assert result is None
    mock_logger.info.assert_any_call(
        "Room '!abc123:matrix.org' is not in matrix_rooms configuration, ignoring invite"
    )


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@patch("mmrelay.matrix_utils.logger")
async def test_on_invite_joins_mapped_room(mock_logger: MagicMock) -> None:
    """
    Test that on_invite joins rooms that are mapped.
    """
    mock_room = MagicMock()
    mock_room.room_id = "!abc123:matrix.org"

    mock_event = MagicMock()
    mock_event.state_key = "@bot:matrix.org"
    mock_event.membership = "invite"
    mock_event.sender = "@inviter:matrix.org"

    mock_client = AsyncMock()
    mock_client.rooms = {}
    mock_join_response = MagicMock()
    mock_join_response.room_id = "!abc123:matrix.org"
    mock_client.join.return_value = mock_join_response

    import mmrelay.matrix_utils

    mmrelay.matrix_utils.matrix_client = mock_client
    mmrelay.matrix_utils.bot_user_id = "@bot:matrix.org"
    mmrelay.matrix_utils.matrix_rooms = [
        {"id": "!abc123:matrix.org", "meshtastic_channel": 0}
    ]

    result = await on_invite(mock_room, mock_event)

    assert result is None
    mock_logger.info.assert_any_call(
        "Room '!abc123:matrix.org' is in matrix_rooms configuration, accepting invite"
    )
    mock_logger.info.assert_any_call("Joining mapped room '!abc123:matrix.org'...")
    mock_logger.info.assert_any_call("Successfully joined room '!abc123:matrix.org'")
    mock_client.join.assert_called_once_with("!abc123:matrix.org")


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@patch("mmrelay.matrix_utils.logger")
async def test_on_invite_already_in_room(mock_logger: MagicMock) -> None:
    """
    Test that on_invite skips joining if already in the room.
    """
    mock_room = MagicMock()
    mock_room.room_id = "!abc123:matrix.org"

    mock_event = MagicMock()
    mock_event.state_key = "@bot:matrix.org"
    mock_event.membership = "invite"
    mock_event.sender = "@inviter:matrix.org"

    mock_client = AsyncMock()
    mock_client.rooms = {"!abc123:matrix.org": MagicMock()}

    import mmrelay.matrix_utils

    mmrelay.matrix_utils.matrix_client = mock_client
    mmrelay.matrix_utils.bot_user_id = "@bot:matrix.org"
    mmrelay.matrix_utils.matrix_rooms = [
        {"id": "!abc123:matrix.org", "meshtastic_channel": 0}
    ]

    result = await on_invite(mock_room, mock_event)

    assert result is None
    mock_logger.info.assert_any_call(
        "Room '!abc123:matrix.org' is in matrix_rooms configuration, accepting invite"
    )
    mock_logger.debug.assert_any_call(
        "Bot is already in room '!abc123:matrix.org', no action needed"
    )
    mock_client.join.assert_not_called()


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@patch("mmrelay.matrix_utils.logger")
async def test_on_invite_handles_join_failure(mock_logger: MagicMock) -> None:
    """
    Test that on_invite handles join failures gracefully.
    """
    mock_room = MagicMock()
    mock_room.room_id = "!abc123:matrix.org"

    mock_event = MagicMock()
    mock_event.state_key = "@bot:matrix.org"
    mock_event.membership = "invite"
    mock_event.sender = "@inviter:matrix.org"

    mock_client = AsyncMock()
    mock_client.rooms = {}
    mock_error_response = MagicMock()
    mock_error_response.room_id = None
    mock_error_response.message = "Forbidden: you are not allowed to join this room"
    mock_client.join.return_value = mock_error_response

    import mmrelay.matrix_utils

    mmrelay.matrix_utils.matrix_client = mock_client
    mmrelay.matrix_utils.bot_user_id = "@bot:matrix.org"
    mmrelay.matrix_utils.matrix_rooms = [
        {"id": "!abc123:matrix.org", "meshtastic_channel": 0}
    ]

    result = await on_invite(mock_room, mock_event)

    assert result is None
    mock_logger.info.assert_any_call(
        "Room '!abc123:matrix.org' is in matrix_rooms configuration, accepting invite"
    )
    mock_logger.info.assert_any_call("Joining mapped room '!abc123:matrix.org'...")
    mock_logger.error.assert_any_call(
        "Failed to join room '!abc123:matrix.org': Forbidden: you are not allowed to join this room"
    )


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@patch("mmrelay.matrix_utils.logger")
async def test_on_invite_handles_no_client(mock_logger: MagicMock) -> None:
    """
    Verifies that on_invite logs an error and takes no action when the Matrix client is not configured.
    
    Asserts that the function returns None and that an error message indicating the missing client is logged.
    
    Parameters:
        mock_logger (MagicMock): Mocked logger used to assert that the expected error message was emitted.
    """
    mock_room = MagicMock()
    mock_room.room_id = "!abc123:matrix.org"

    mock_event = MagicMock()
    mock_event.state_key = "@bot:matrix.org"
    mock_event.membership = "invite"
    mock_event.sender = "@inviter:matrix.org"

    import mmrelay.matrix_utils

    mmrelay.matrix_utils.matrix_client = None
    mmrelay.matrix_utils.bot_user_id = "@bot:matrix.org"
    mmrelay.matrix_utils.matrix_rooms = [
        {"id": "!abc123:matrix.org", "meshtastic_channel": 0}
    ]

    result = await on_invite(mock_room, mock_event)

    assert result is None
    mock_logger.error.assert_any_call("matrix_client is None, cannot join room")


@pytest.mark.usefixtures("reset_matrix_utils_globals")
@patch("mmrelay.matrix_utils.logger")
async def test_on_invite_with_id_in_mapping(mock_logger: MagicMock) -> None:
    """
    Test that on_invite joins rooms matched by room ID in config.
    """
    mock_room = MagicMock()
    mock_room.room_id = "!abc123:matrix.org"

    mock_event = MagicMock()
    mock_event.state_key = "@bot:matrix.org"
    mock_event.membership = "invite"
    mock_event.sender = "@inviter:matrix.org"

    mock_client = AsyncMock()
    mock_client.rooms = {}
    mock_join_response = MagicMock()
    mock_join_response.room_id = "!abc123:matrix.org"
    mock_client.join.return_value = mock_join_response

    import mmrelay.matrix_utils

    mmrelay.matrix_utils.matrix_client = mock_client
    mmrelay.matrix_utils.bot_user_id = "@bot:matrix.org"
    mmrelay.matrix_utils.matrix_rooms = [
        {"id": "!abc123:matrix.org", "meshtastic_channel": 0}
    ]

    result = await on_invite(mock_room, mock_event)

    assert result is None
    mock_logger.info.assert_any_call(
        "Room '!abc123:matrix.org' is in matrix_rooms configuration, accepting invite"
    )
    mock_client.join.assert_called_once_with("!abc123:matrix.org")