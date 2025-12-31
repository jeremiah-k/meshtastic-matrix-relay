from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from mmrelay.matrix_utils import (
    NioLocalProtocolError,
    _display_room_channel_mappings,
    _is_room_alias,
    _iter_room_alias_entries,
    _resolve_aliases_in_mapping,
    _update_room_id_in_mapping,
    join_matrix_room,
)

# Join Room Tests


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.matrix_client")
@patch("mmrelay.matrix_utils.logger")
async def test_join_matrix_room_by_id(_mock_logger, mock_matrix_client):
    """
    Test that joining a Matrix room by its room ID calls the client's join method with the correct argument.
    """
    mock_matrix_client.rooms = {}
    mock_matrix_client.join = AsyncMock(
        return_value=SimpleNamespace(room_id="!room:matrix.org")
    )

    await join_matrix_room(mock_matrix_client, "!room:matrix.org")

    mock_matrix_client.join.assert_called_once_with("!room:matrix.org")


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.matrix_client")
@patch("mmrelay.matrix_utils.logger")
async def test_join_matrix_room_already_joined(_mock_logger, mock_matrix_client):
    """Test that join_matrix_room does nothing if already in the room."""
    mock_matrix_client.rooms = {"!room:matrix.org": MagicMock()}
    mock_matrix_client.join = AsyncMock()

    await join_matrix_room(mock_matrix_client, "!room:matrix.org")

    mock_matrix_client.join.assert_not_called()
    _mock_logger.debug.assert_called_with(
        "Bot is already in room '%s', no action needed.",
        "!room:matrix.org",
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.logger")
async def test_join_matrix_room_resolves_alias(mock_logger, monkeypatch):
    mock_client = MagicMock()
    mock_client.rooms = {}
    resolved_id = "!resolved:matrix.org"
    mock_client.room_resolve_alias = AsyncMock(
        return_value=SimpleNamespace(room_id=resolved_id)
    )
    mock_client.join = AsyncMock()
    matrix_rooms_config = [{"id": "#alias:matrix.org"}]
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms", matrix_rooms_config, raising=False
    )

    await join_matrix_room(mock_client, "#alias:matrix.org")

    mock_client.room_resolve_alias.assert_awaited_once_with("#alias:matrix.org")
    mock_client.join.assert_awaited_once_with(resolved_id)
    mock_logger.info.assert_any_call(
        "Resolved alias '%s' -> '%s'", "#alias:matrix.org", resolved_id
    )
    assert matrix_rooms_config[0]["id"] == resolved_id


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.logger")
async def test_join_matrix_room_resolve_alias_handles_nio_errors(
    mock_logger, monkeypatch
):
    """
    Alias resolution should catch expected nio exceptions without masking programmer errors.
    """
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.room_resolve_alias = AsyncMock(side_effect=NioLocalProtocolError("bad"))
    mock_client.join = AsyncMock()
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms",
        [{"id": "#alias:matrix.org"}],
        raising=False,
    )

    await join_matrix_room(mock_client, "#alias:matrix.org")

    mock_client.room_resolve_alias.assert_awaited_once()
    mock_client.join.assert_not_awaited()
    mock_logger.exception.assert_called_once()


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.logger")
async def test_join_matrix_room_resolve_alias_missing_room_id(mock_logger, monkeypatch):
    """If alias resolution returns no room_id, the function should log and return without joining."""
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.room_resolve_alias = AsyncMock(
        return_value=SimpleNamespace(message="no room")
    )
    mock_client.join = AsyncMock()
    matrix_rooms_config = [{"id": "#alias:matrix.org"}]
    monkeypatch.setattr(
        "mmrelay.matrix_utils.matrix_rooms", matrix_rooms_config, raising=False
    )

    await join_matrix_room(mock_client, "#alias:matrix.org")

    mock_client.room_resolve_alias.assert_awaited_once_with("#alias:matrix.org")
    mock_client.join.assert_not_awaited()
    mock_logger.error.assert_any_call(
        "Failed to resolve alias '%s': %s", "#alias:matrix.org", "no room"
    )


@pytest.mark.asyncio
@patch("mmrelay.matrix_utils.logger")
async def test_join_matrix_room_rejects_non_string_identifier(mock_logger):
    mock_client = MagicMock()
    mock_client.rooms = {}
    mock_client.join = AsyncMock()

    await join_matrix_room(mock_client, 12345)  # type: ignore[arg-type]

    mock_client.join.assert_not_called()
    mock_logger.error.assert_called_with(
        "join_matrix_room expected a string room ID, received %r",
        12345,
    )


# Alias Utility Tests


def test_is_room_alias_with_alias():
    """Test _is_room_alias returns True for room aliases starting with '#'."""
    assert _is_room_alias("#room:matrix.org") is True
    assert _is_room_alias("#alias") is True


def test_is_room_alias_with_room_id():
    """Test _is_room_alias returns False for room IDs."""
    assert _is_room_alias("!room:matrix.org") is False
    assert _is_room_alias("room_id") is False


def test_is_room_alias_with_non_string():
    """Test _is_room_alias returns False for non-string inputs."""
    assert _is_room_alias(123) is False
    assert _is_room_alias(None) is False
    assert _is_room_alias([]) is False


def test_iter_room_alias_entries_list_with_strings():
    """Test _iter_room_alias_entries yields string entries from a list."""
    mapping = ["#room1:matrix.org", "!room2:matrix.org", "#room3:matrix.org"]

    entries = list(_iter_room_alias_entries(mapping))
    assert len(entries) == 3

    # Assert all expected alias_or_id values are present
    aliases_or_ids = {entry[0] for entry in entries}
    assert aliases_or_ids == {
        "#room1:matrix.org",
        "!room2:matrix.org",
        "#room3:matrix.org",
    }

    # Test setters for all entries.
    for alias, setter in entries:
        if alias == "#room1:matrix.org":
            setter("!resolved1:matrix.org")
        elif alias == "!room2:matrix.org":
            setter("!resolved2:matrix.org")
        elif alias == "#room3:matrix.org":
            setter("!resolved3:matrix.org")
    assert mapping[0] == "!resolved1:matrix.org"
    assert mapping[1] == "!resolved2:matrix.org"
    assert mapping[2] == "!resolved3:matrix.org"


def test_iter_room_alias_entries_list_with_dicts():
    """Test _iter_room_alias_entries yields dict entries from a list."""
    mapping = [
        {"id": "#room1:matrix.org", "channel": 0},
        {"id": "!room2:matrix.org", "channel": 1},
        {"channel": 2},  # No id key
    ]

    entries = list(_iter_room_alias_entries(mapping))
    assert len(entries) == 3

    aliases_or_ids = {entry[0] for entry in entries}
    assert aliases_or_ids == {"#room1:matrix.org", "!room2:matrix.org", ""}

    for alias, setter in entries:
        if alias == "#room1:matrix.org":
            setter("!resolved1:matrix.org")
        elif alias == "!room2:matrix.org":
            setter("!new-room2:matrix.org")
        elif alias == "":
            setter("!resolved3:matrix.org")
    assert mapping[0]["id"] == "!resolved1:matrix.org"
    assert mapping[1]["id"] == "!new-room2:matrix.org"
    assert mapping[2]["id"] == "!resolved3:matrix.org"


def test_iter_room_alias_entries_dict_with_strings():
    """Test _iter_room_alias_entries yields string values from a dict."""
    mapping = {
        "room1": "#alias1:matrix.org",
        "room2": "!room2:matrix.org",
        "room3": "#alias3:matrix.org",
    }

    entries = list(_iter_room_alias_entries(mapping))
    assert len(entries) == 3

    aliases_or_ids = [entry[0] for entry in entries]
    assert set(aliases_or_ids) == {
        "#alias1:matrix.org",
        "!room2:matrix.org",
        "#alias3:matrix.org",
    }

    for alias, setter in entries:
        if alias == "#alias1:matrix.org":
            setter("!resolved1:matrix.org")
        elif alias == "!room2:matrix.org":
            setter("!new-room2:matrix.org")
        elif alias == "#alias3:matrix.org":
            setter("!resolved3:matrix.org")
    assert mapping["room1"] == "!resolved1:matrix.org"
    assert mapping["room2"] == "!new-room2:matrix.org"
    assert mapping["room3"] == "!resolved3:matrix.org"


def test_iter_room_alias_entries_dict_with_dicts():
    """Test _iter_room_alias_entries yields dict values from a dict."""
    mapping = {
        "room1": {"id": "#alias1:matrix.org", "channel": 0},
        "room2": {"id": "!room2:matrix.org", "channel": 1},
        "room3": {"channel": 2},  # No id key
    }

    entries = list(_iter_room_alias_entries(mapping))
    assert len(entries) == 3

    for alias, setter in entries:
        if alias == "#alias1:matrix.org":
            setter("!resolved1:matrix.org")
        elif alias == "!room2:matrix.org":
            setter("!resolved2:matrix.org")
        elif alias == "":
            setter("!resolved3:matrix.org")
    assert mapping["room1"]["id"] == "!resolved1:matrix.org"
    assert mapping["room2"]["id"] == "!resolved2:matrix.org"
    assert mapping["room3"]["id"] == "!resolved3:matrix.org"


@pytest.mark.asyncio
async def test_resolve_aliases_in_mapping_list():
    """Test _resolve_aliases_in_mapping resolves aliases in a list."""
    mapping = [
        "#room1:matrix.org",
        "!room2:matrix.org",
        {"id": "#room3:matrix.org", "channel": 2},
    ]

    async def mock_resolver(alias):
        """
        Resolve specific Matrix room aliases to canonical room IDs.

        Parameters:
                alias (str): Matrix room alias or identifier to resolve.

        Returns:
                str: The canonical room ID for known aliases (`!resolved1:matrix.org` for `#room1:matrix.org`, `!resolved3:matrix.org` for `#room3:matrix.org`), or the original `alias` unchanged.
        """
        if alias == "#room1:matrix.org":
            return "!resolved1:matrix.org"
        elif alias == "#room3:matrix.org":
            return "!resolved3:matrix.org"
        return alias

    await _resolve_aliases_in_mapping(mapping, mock_resolver)

    assert mapping[0] == "!resolved1:matrix.org"
    assert mapping[1] == "!room2:matrix.org"  # Already resolved
    assert mapping[2]["id"] == "!resolved3:matrix.org"


@pytest.mark.asyncio
async def test_resolve_aliases_in_mapping_dict():
    """Test _resolve_aliases_in_mapping resolves aliases in a dict."""
    mapping = {
        "room1": "#alias1:matrix.org",
        "room2": "!room2:matrix.org",
        "room3": {"id": "#alias3:matrix.org", "channel": 2},
    }

    async def mock_resolver(alias):
        """
        Resolve test Matrix room aliases to predefined room IDs, falling back to the input.

        Parameters:
            alias (str): Matrix room alias or identifier to resolve.

        Returns:
            str: The resolved Matrix room ID for known aliases, otherwise the original identifier.
        """
        if alias == "#alias1:matrix.org":
            return "!resolved1:matrix.org"
        elif alias == "#alias3:matrix.org":
            return "!resolved3:matrix.org"
        return alias

    await _resolve_aliases_in_mapping(mapping, mock_resolver)

    assert mapping["room1"] == "!resolved1:matrix.org"
    assert mapping["room2"] == "!room2:matrix.org"  # Already resolved
    assert mapping["room3"]["id"] == "!resolved3:matrix.org"


def test_update_room_id_in_mapping_list():
    """Test _update_room_id_in_mapping updates room ID in a list."""
    mapping = ["!old_room:matrix.org", "!other_room:matrix.org"]

    result = _update_room_id_in_mapping(
        mapping, "!old_room:matrix.org", "!new_room:matrix.org"
    )
    assert result is True
    assert mapping[0] == "!new_room:matrix.org"
    assert mapping[1] == "!other_room:matrix.org"


def test_update_room_id_in_mapping_list_dict():
    """Test _update_room_id_in_mapping updates room ID in a list of dicts."""
    mapping = [
        {"id": "!old_room:matrix.org", "channel": 0},
        {"id": "!other_room:matrix.org", "channel": 1},
    ]

    result = _update_room_id_in_mapping(
        mapping, "!old_room:matrix.org", "!new_room:matrix.org"
    )
    assert result is True
    assert mapping[0]["id"] == "!new_room:matrix.org"
    assert mapping[1]["id"] == "!other_room:matrix.org"


def test_update_room_id_in_mapping_dict():
    """Test _update_room_id_in_mapping updates room ID in a dict."""
    mapping = {"room1": "!old_room:matrix.org", "room2": "!other_room:matrix.org"}

    result = _update_room_id_in_mapping(
        mapping, "!old_room:matrix.org", "!new_room:matrix.org"
    )
    assert result is True
    assert mapping["room1"] == "!new_room:matrix.org"
    assert mapping["room2"] == "!other_room:matrix.org"


def test_update_room_id_in_mapping_dict_dicts():
    """Test _update_room_id_in_mapping updates room ID in a dict of dicts."""
    mapping = {
        "room1": {"id": "!old_room:matrix.org", "channel": 0},
        "room2": {"id": "!other_room:matrix.org", "channel": 1},
    }

    result = _update_room_id_in_mapping(
        mapping, "!old_room:matrix.org", "!new_room:matrix.org"
    )
    assert result is True
    assert mapping["room1"]["id"] == "!new_room:matrix.org"
    assert mapping["room2"]["id"] == "!other_room:matrix.org"


def test_update_room_id_in_mapping_not_found():
    """
    Test that _update_room_id_in_mapping returns False when alias is not found in mapping.
    """
    mapping = {"#alias1": "room1", "#alias2": "room2"}

    result = _update_room_id_in_mapping(mapping, "#nonexistent", "!resolved:matrix.org")

    assert result is False


def test_iter_room_alias_entries_complex_nested():
    """
    Test _iter_room_alias_entries with complex nested structures.
    """
    # Test with list containing mixed string and dict entries
    mapping_list = [
        "#alias1",
        {"id": "#alias2", "meshtastic_channel": 1},
        {"id": "#alias3", "extra": "data"},
    ]

    entries = list(_iter_room_alias_entries(mapping_list))

    # Should yield 3 entries
    assert len(entries) == 3

    # Check first entry (string)
    alias1, setter1 = entries[0]
    assert alias1 == "#alias1"

    # Check second entry (dict with id)
    alias2, setter2 = entries[1]
    assert alias2 == "#alias2"

    # Check third entry (dict with id and extra data)
    alias3, setter3 = entries[2]
    assert alias3 == "#alias3"

    # Test setters work correctly
    setter1("!resolved1")
    assert mapping_list[0] == "!resolved1"

    setter2("!resolved2")
    assert mapping_list[1]["id"] == "!resolved2"

    setter3("!resolved3")
    assert mapping_list[2]["id"] == "!resolved3"


def test_iter_room_alias_entries_dict_format():
    """
    Test _iter_room_alias_entries with dictionary format.
    """
    mapping_dict = {
        "room1": "#alias1",
        "room2": {"id": "#alias2", "meshtastic_channel": 1},
        "room3": {"id": "#alias3", "extra": "data"},
    }

    entries = list(_iter_room_alias_entries(mapping_dict))

    # Should yield 3 entries
    assert len(entries) == 3

    # Check entries
    alias1, setter1 = entries[0]
    assert alias1 == "#alias1"

    alias2, setter2 = entries[1]
    assert alias2 == "#alias2"

    alias3, setter3 = entries[2]
    assert alias3 == "#alias3"

    # Test setters work correctly
    setter1("!resolved1")
    assert mapping_dict["room1"] == "!resolved1"

    setter2("!resolved2")
    assert mapping_dict["room2"]["id"] == "!resolved2"

    setter3("!resolved3")
    assert mapping_dict["room3"]["id"] == "!resolved3"


def test_iter_room_alias_entries_empty_id():
    """
    Test _iter_room_alias_entries handles entries without id field.
    """
    mapping = [
        {"meshtastic_channel": 1},  # Missing id
        {"id": "", "meshtastic_channel": 2},  # Empty id
        {"id": "#alias3", "meshtastic_channel": 3},  # Valid id
    ]

    entries = list(_iter_room_alias_entries(mapping))

    # Should yield 3 entries
    assert len(entries) == 3

    # Check empty id handling
    alias1, _setter1 = entries[0]
    assert alias1 == ""

    alias2, _setter2 = entries[1]
    assert alias2 == ""

    alias3, _setter3 = entries[2]
    assert alias3 == "#alias3"


@pytest.mark.asyncio
async def test_resolve_aliases_in_mapping_unsupported_type():
    """
    Test that _resolve_aliases_in_mapping handles unsupported mapping types gracefully.
    """
    mock_resolver = AsyncMock(return_value="!resolved:matrix.org")

    # Test with unsupported type (string instead of list/dict)
    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        await _resolve_aliases_in_mapping("not_a_mapping", mock_resolver)

        # Should log warning and return without error
        mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_aliases_in_mapping_resolver_failure():
    """
    Test that _resolve_aliases_in_mapping handles resolver failures gracefully.
    """
    mapping = {"#alias1": "room1", "#alias2": "room2"}
    mock_resolver = AsyncMock(return_value=None)  # Resolver fails

    with patch("mmrelay.matrix_utils.logger"):
        await _resolve_aliases_in_mapping(mapping, mock_resolver)

        # Should not modify mapping when resolver returns None
        assert mapping == {"#alias1": "room1", "#alias2": "room2"}


@pytest.mark.parametrize(
    "e2ee_status, expected_log_for_room1",
    [
        ({"overall_status": "ready"}, "    🔒 Room 1"),
        (
            {"overall_status": "unavailable"},
            "    ⚠️ Room 1 (E2EE not supported - messages blocked)",
        ),
        (
            {"overall_status": "disabled"},
            "    ⚠️ Room 1 (E2EE disabled - messages blocked)",
        ),
        (
            {"overall_status": "incomplete"},
            "    ⚠️ Room 1 (E2EE incomplete - messages may be blocked)",
        ),
    ],
    ids=["e2ee_ready", "e2ee_unavailable", "e2ee_disabled", "e2ee_incomplete"],
)
def test_display_room_channel_mappings(e2ee_status, expected_log_for_room1):
    """Test _display_room_channel_mappings logs room-channel mappings for various E2EE statuses."""

    rooms = {
        "!room1:matrix.org": MagicMock(display_name="Room 1", encrypted=True),
        "!room2:matrix.org": MagicMock(display_name="Room 2", encrypted=False),
    }
    config = {
        "matrix_rooms": [
            {"id": "!room1:matrix.org", "meshtastic_channel": 0},
            {"id": "!room2:matrix.org", "meshtastic_channel": 1},
        ]
    }

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        _display_room_channel_mappings(rooms, config, e2ee_status)

        # Should have logged room mappings in order
        expected_calls = [
            call("Meshtastic Channels ↔ Matrix Rooms (2 configured):"),
            call("  Channel 0:"),
            call(expected_log_for_room1),
            call("  Channel 1:"),
            call("    ✅ Room 2"),
        ]
        mock_logger.info.assert_has_calls(expected_calls)


def test_display_room_channel_mappings_empty():
    """Test _display_room_channel_mappings with no rooms."""

    rooms = {}
    config = {"matrix_rooms": []}
    e2ee_status = {"overall_status": "ready"}

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        _display_room_channel_mappings(rooms, config, e2ee_status)

        mock_logger.info.assert_called_with("Bot is not in any Matrix rooms")


def test_display_room_channel_mappings_no_config():
    """Test _display_room_channel_mappings with missing config."""

    rooms = {"!room1:matrix.org": MagicMock()}
    config = {}
    e2ee_status = {"overall_status": "ready"}

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        _display_room_channel_mappings(rooms, config, e2ee_status)

        mock_logger.info.assert_called_with("No matrix_rooms configuration found")


def test_display_room_channel_mappings_dict_config():
    """Test _display_room_channel_mappings with dict format matrix_rooms config."""

    rooms = {
        "!room1:matrix.org": MagicMock(display_name="Room 1", encrypted=False),
    }
    config = {
        "matrix_rooms": {
            "room1": {"id": "!room1:matrix.org", "meshtastic_channel": 0},
        }
    }
    e2ee_status = {"overall_status": "ready"}

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        _display_room_channel_mappings(rooms, config, e2ee_status)

        expected_calls = [
            call("Meshtastic Channels ↔ Matrix Rooms (1 configured):"),
            call("  Channel 0:"),
            call("    ✅ Room 1"),
        ]
        mock_logger.info.assert_has_calls(expected_calls)


def test_display_room_channel_mappings_no_display_name():
    """Test _display_room_channel_mappings with rooms lacking display_name."""

    rooms = {
        "!room1:matrix.org": MagicMock(spec=["encrypted"]),  # No display_name
    }
    config = {
        "matrix_rooms": [
            {"id": "!room1:matrix.org", "meshtastic_channel": 0},
        ]
    }
    e2ee_status = {"overall_status": "ready"}

    with patch("mmrelay.matrix_utils.logger") as mock_logger:
        _display_room_channel_mappings(rooms, config, e2ee_status)

        expected_calls = [
            call("Meshtastic Channels ↔ Matrix Rooms (1 configured):"),
            call("  Channel 0:"),
            call("    🔒 !room1:matrix.org"),  # Should fall back to room_id
        ]
        mock_logger.info.assert_has_calls(expected_calls)
