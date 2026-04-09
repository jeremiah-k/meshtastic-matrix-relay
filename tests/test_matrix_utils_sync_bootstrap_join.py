"""Tests for join_matrix_room edge cases.

Covers alias resolution, room joining, and error handling.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.matrix_utils import join_matrix_room


@pytest.mark.asyncio
async def test_join_matrix_room_alias_resolution_success():
    mock_client = MagicMock()
    mock_client.rooms = {"!resolved:matrix.org": MagicMock()}
    mock_client.room_resolve_alias = AsyncMock(
        return_value=MagicMock(room_id="!resolved:matrix.org")
    )

    with (
        patch(
            "mmrelay.matrix_utils.matrix_rooms",
            {"#test:matrix.org": {"id": "!resolved:matrix.org"}},
        ),
        patch("mmrelay.matrix_utils._update_room_id_in_mapping") as mock_update,
        patch("mmrelay.matrix_utils.logger"),
    ):
        await join_matrix_room(mock_client, "#test:matrix.org")

    mock_client.room_resolve_alias.assert_awaited_once_with("#test:matrix.org")
    mock_update.assert_called_once()


@pytest.mark.asyncio
async def test_join_matrix_room_alias_resolution_fails():
    from mmrelay.matrix_utils import NioLocalTransportError

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.room_resolve_alias = AsyncMock(
        side_effect=NioLocalTransportError("error")
    )

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        await join_matrix_room(mock_client, "#test:matrix.org")

    assert any(
        "Error resolving alias" in str(call.args[0])
        for call in mock_logger.exception.call_args_list
    )


@pytest.mark.asyncio
async def test_join_matrix_room_alias_no_room_id():
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_response = MagicMock()
    mock_response.room_id = None
    mock_response.message = "not found"
    mock_client.room_resolve_alias = AsyncMock(return_value=mock_response)

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        await join_matrix_room(mock_client, "#test:matrix.org")

    assert any(
        "Failed to resolve alias" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_join_matrix_room_alias_resolution_no_response():
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.room_resolve_alias = AsyncMock(return_value=None)

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        await join_matrix_room(mock_client, "#test:matrix.org")

    assert any(
        "Failed to resolve alias" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_join_matrix_room_already_joined():
    mock_client = MagicMock()
    mock_room = MagicMock()
    mock_client.rooms = {"!room:matrix.org": mock_room}

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        await join_matrix_room(mock_client, "!room:matrix.org")

    assert any(
        "already in room" in str(call.args[0]).lower()
        for call in mock_logger.debug.call_args_list
    )


@pytest.mark.asyncio
async def test_join_matrix_room_join_success():
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.join = AsyncMock(return_value=MagicMock(room_id="!room:matrix.org"))

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        await join_matrix_room(mock_client, "!room:matrix.org")

    assert any(
        "Joined room" in str(call.args[0]) for call in mock_logger.info.call_args_list
    )


@pytest.mark.asyncio
async def test_join_matrix_room_join_failure_no_room_id():
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_response = MagicMock()
    mock_response.room_id = None
    mock_response.message = "forbidden"
    mock_client.join = AsyncMock(return_value=mock_response)

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        await join_matrix_room(mock_client, "!room:matrix.org")

    assert any(
        "Failed to join room" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_join_matrix_room_join_nio_exception():
    from mmrelay.matrix_utils import NioLocalTransportError

    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.join = AsyncMock(side_effect=NioLocalTransportError("net error"))

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        await join_matrix_room(mock_client, "!room:matrix.org")

    assert any(
        "Error joining room" in str(call.args[0])
        for call in mock_logger.exception.call_args_list
    )


@pytest.mark.asyncio
async def test_join_matrix_room_join_unexpected_exception():
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.join = AsyncMock(side_effect=RuntimeError("unexpected"))

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        await join_matrix_room(mock_client, "!room:matrix.org")

    assert any(
        "Unexpected error joining room" in str(call.args[0])
        for call in mock_logger.exception.call_args_list
    )


@pytest.mark.asyncio
async def test_join_matrix_room_non_string_input():
    mock_client = MagicMock()

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        await join_matrix_room(mock_client, 123)  # type: ignore

    assert any(
        "expected a string" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )


@pytest.mark.asyncio
async def test_join_matrix_room_alias_with_mapping_update_error():
    mock_client = MagicMock()
    mock_client.rooms = {"!resolved:matrix.org": MagicMock()}
    mock_client.room_resolve_alias = AsyncMock(
        return_value=MagicMock(room_id="!resolved:matrix.org")
    )

    with (
        patch(
            "mmrelay.matrix_utils.matrix_rooms",
            {"#test:matrix.org": {"id": "#test:matrix.org"}},
        ),
        patch(
            "mmrelay.matrix_utils._update_room_id_in_mapping",
            side_effect=ValueError("update error"),
        ),
        patch("mmrelay.matrix_utils.logger") as mock_logger,
    ):
        await join_matrix_room(mock_client, "#test:matrix.org")

    assert any(
        "Non-fatal error updating matrix_rooms" in str(call.args[0])
        for call in mock_logger.debug.call_args_list
    )


@pytest.mark.asyncio
async def test_join_matrix_room_alias_with_none_mapping():
    mock_client = MagicMock()
    mock_client.rooms = {"!resolved:matrix.org": MagicMock()}
    mock_client.room_resolve_alias = AsyncMock(
        return_value=MagicMock(room_id="!resolved:matrix.org")
    )

    with (
        patch("mmrelay.matrix_utils.matrix_rooms", None),
        patch("mmrelay.matrix_utils.logger"),
    ):
        await join_matrix_room(mock_client, "#test:matrix.org")

    mock_client.room_resolve_alias.assert_awaited_once()


@pytest.mark.asyncio
async def test_join_matrix_room_join_no_response():
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.join = AsyncMock(return_value=None)

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        await join_matrix_room(mock_client, "!room:matrix.org")

    assert any(
        "Failed to join room" in str(call.args[0])
        for call in mock_logger.error.call_args_list
    )
