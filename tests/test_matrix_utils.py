import os
import re
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.cli_utils import _cleanup_local_session_data, logout_matrix_bot
from mmrelay.config import get_e2ee_store_dir, load_credentials, save_credentials
from mmrelay.matrix_utils import (
    _add_truncated_vars,
    _can_auto_create_credentials,
    _create_mapping_info,
    _get_msgs_to_keep_config,
    bot_command,
    connect_matrix,
    format_reply_message,
    get_interaction_settings,
    get_matrix_prefix,
    get_meshtastic_prefix,
    get_user_display_name,
    join_matrix_room,
    login_matrix_bot,
    matrix_relay,
    message_storage_enabled,
    on_room_message,
    send_reply_to_meshtastic,
    send_room_image,
    strip_quoted_lines,
    truncate_message,
    upload_image,
    validate_prefix_format,
)

# Matrix room message handling tests - converted from unittest.TestCase to standalone pytest functions
#
# Conversion rationale:
# - Improved readability with native assert statements instead of self.assertEqual()
# - Better integration with pytest fixtures for test setup and teardown
# - Simplified async test execution without explicit asyncio.run() calls
# - Enhanced test isolation and maintainability
# - Alignment with modern Python testing practices


@pytest.fixture
def mock_room():
    """Mock Matrix room fixture for testing room message handling."""
    mock_room = MagicMock()
    mock_room.room_id = "!room:matrix.org"
    return mock_room


@pytest.fixture
def mock_event():
    """Mock Matrix event fixture for testing message events."""
    mock_event = MagicMock()
    mock_event.sender = "@user:matrix.org"
    mock_event.body = "Hello, world!"
    mock_event.source = {"content": {"body": "Hello, world!"}}
    mock_event.server_timestamp = 1234567890
    return mock_event


@pytest.fixture
def test_config():
    """Test configuration fixture with Meshtastic and Matrix settings."""
    return {
        "meshtastic": {
            "broadcast_enabled": True,
            "prefix_enabled": True,
            "prefix_format": "{display5}[M]: ",
            "message_interactions": {"reactions": False, "replies": False},
            "meshnet_name": "test_mesh",
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        "matrix": {"bot_user_id": "@bot:matrix.org"},
    }


@patch("mmrelay.matrix_utils.connect_meshtastic")
@patch("mmrelay.matrix_utils.queue_message")
@patch("mmrelay.matrix_utils.bot_start_time", 1234567880)
@patch("mmrelay.matrix_utils.get_user_display_name")
@patch("mmrelay.matrix_utils.isinstance")
async def test_on_room_message_simple_text(
    mock_isinstance,
    mock_get_user_display_name,
    mock_queue_message,
    mock_connect_meshtastic,
    mock_room,
    mock_event,
    test_config,
):
    """
    Test that a non-reaction text message event is processed and queued for Meshtastic relay.

    Ensures that when a user sends a simple text message, the message is correctly queued with the expected content for relaying.
    """
    # Use real isinstance for this test so type checks on strings behave normally
    import builtins

    mock_isinstance.side_effect = builtins.isinstance

    # Create a proper async mock function
    async def mock_get_user_display_name_func(*args, **kwargs):
        """
        Asynchronously returns a fixed user display name string "user".

        Intended for use as a mock replacement in tests requiring an async display name retrieval function.
        """
        return "user"

    mock_get_user_display_name.side_effect = mock_get_user_display_name_func
    with patch("mmrelay.matrix_utils.config", test_config), patch(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]
    ), patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]):
        # Mock the matrix client - use MagicMock to prevent coroutine warnings
        mock_matrix_client = MagicMock()
        with patch("mmrelay.matrix_utils.matrix_client", mock_matrix_client):
            # Run the function
            await on_room_message(mock_room, mock_event)

            # Assert that the message was queued
            mock_queue_message.assert_called_once()
            call_args = mock_queue_message.call_args[1]
            assert "Hello, world!" in call_args["text"]


@patch("mmrelay.matrix_utils.connect_meshtastic")
@patch("mmrelay.matrix_utils.queue_message")
@patch("mmrelay.matrix_utils.bot_start_time", 1234567880)
@patch("mmrelay.matrix_utils.isinstance")
async def test_on_room_message_remote_prefers_meshtastic_text(
    mock_isinstance,
    mock_queue_message,
    mock_connect_meshtastic,
    mock_room,
    mock_event,
    test_config,
):
    """Ensure remote mesh messages fall back to raw meshtastic_text when body is empty."""

    import builtins

    mock_isinstance.side_effect = builtins.isinstance
    mock_event.body = ""
    mock_event.source = {
        "content": {
            "body": "",
            "meshtastic_longname": "LoRa",
            "meshtastic_shortname": "Trak",
            "meshtastic_meshnet": "remote",
            "meshtastic_text": "Hello from remote mesh",
            "meshtastic_portnum": "TEXT_MESSAGE_APP",
        }
    }

    # Remote mesh must differ from local meshnet_name to exercise relay path
    test_config["meshtastic"]["meshnet_name"] = "local_mesh"

    matrix_rooms = test_config["matrix_rooms"]
    with patch("mmrelay.matrix_utils.config", test_config), patch(
        "mmrelay.matrix_utils.matrix_rooms", matrix_rooms
    ), patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]):
        mock_matrix_client = MagicMock()
        with patch("mmrelay.matrix_utils.matrix_client", mock_matrix_client):
            await on_room_message(mock_room, mock_event)

    mock_queue_message.assert_called_once()
    queued_kwargs = mock_queue_message.call_args.kwargs
    assert "Hello from remote mesh" in queued_kwargs["text"]


@patch("mmrelay.matrix_utils.connect_meshtastic")
@patch("mmrelay.matrix_utils.queue_message")
@patch("mmrelay.matrix_utils.bot_start_time", 1234567880)
async def test_on_room_message_ignore_bot(
    mock_queue_message, mock_connect_meshtastic, mock_room, mock_event, test_config
):
    """
    Test that messages sent by the bot user are ignored and not relayed to Meshtastic.

    Ensures that when the event sender matches the configured bot user ID, the message is not queued for relay.
    """
    mock_event.sender = test_config["matrix"]["bot_user_id"]
    with patch("mmrelay.matrix_utils.config", test_config), patch(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]
    ), patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]):
        # Mock the matrix client - use MagicMock to prevent coroutine warnings
        mock_matrix_client = MagicMock()
        with patch("mmrelay.matrix_utils.matrix_client", mock_matrix_client):
            # Run the function
            await on_room_message(mock_room, mock_event)

            # Assert that the message was not queued
            mock_queue_message.assert_not_called()


@patch("mmrelay.matrix_utils.connect_meshtastic")
@patch("mmrelay.matrix_utils.queue_message")
@patch("mmrelay.matrix_utils.bot_start_time", 1234567880)
@patch("mmrelay.matrix_utils.get_message_map_by_matrix_event_id")
@patch("mmrelay.matrix_utils.get_user_display_name")
@patch("mmrelay.matrix_utils.isinstance")
async def test_on_room_message_reply_enabled(
    mock_isinstance,
    mock_get_user_display_name,
    mock_get_message_map,
    mock_queue_message,
    mock_connect_meshtastic,
    mock_room,
    mock_event,
    test_config,
):
    """
    Test that reply messages are processed and queued when reply interactions are enabled.

    Ensures that when a Matrix event is a reply and reply interactions are enabled in the configuration, the reply text (with quoted lines removed) is extracted and passed to the Meshtastic message queue.
    """
    mock_isinstance.return_value = False

    # Create a proper async mock function
    async def mock_get_user_display_name_func(*args, **kwargs):
        """
        Asynchronously returns a fixed user display name string "user".

        Intended for use as a mock replacement in tests requiring an async display name retrieval function.
        """
        return "user"

    mock_get_user_display_name.side_effect = mock_get_user_display_name_func
    test_config["meshtastic"]["message_interactions"]["replies"] = True
    mock_event.source = {
        "content": {
            "m.relates_to": {"m.in_reply_to": {"event_id": "original_event_id"}}
        }
    }
    mock_event.body = (
        "> <@original_user:matrix.org> original message\n\nThis is a reply"
    )
    mock_get_message_map.return_value = (
        "meshtastic_id",
        "!room:matrix.org",
        "original_text",
        "test_mesh",
    )

    with patch("mmrelay.matrix_utils.config", test_config), patch(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]
    ), patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]):
        # Mock the matrix client
        mock_matrix_client = MagicMock()
        with patch("mmrelay.matrix_utils.matrix_client", mock_matrix_client):
            # Run the function
            await on_room_message(mock_room, mock_event)

            # Assert that the message was queued
            mock_queue_message.assert_called_once()
            call_args = mock_queue_message.call_args[1]
            assert "This is a reply" in call_args["text"]


@patch("mmrelay.matrix_utils.connect_meshtastic")
@patch("mmrelay.matrix_utils.queue_message")
@patch("mmrelay.matrix_utils.bot_start_time", 1234567880)
@patch("mmrelay.matrix_utils.get_user_display_name")
@patch("mmrelay.matrix_utils.isinstance")
async def test_on_room_message_reply_disabled(
    mock_isinstance,
    mock_get_user_display_name,
    mock_queue_message,
    mock_connect_meshtastic,
    mock_room,
    mock_event,
    test_config,
):
    """
    Test that reply messages are relayed with full content when reply interactions are disabled.

    Ensures that when reply interactions are disabled in the configuration, the entire event body—including quoted original messages—is queued for Meshtastic relay without stripping quoted lines.
    """
    mock_isinstance.return_value = False

    # Create a proper async mock function
    async def mock_get_user_display_name_func(*args, **kwargs):
        """
        Asynchronously returns a fixed user display name string "user".

        Intended for use as a mock replacement in tests requiring an async display name retrieval function.
        """
        return "user"

    mock_get_user_display_name.side_effect = mock_get_user_display_name_func
    test_config["meshtastic"]["message_interactions"]["replies"] = False
    mock_event.source = {
        "content": {
            "m.relates_to": {"m.in_reply_to": {"event_id": "original_event_id"}}
        }
    }
    mock_event.body = (
        "> <@original_user:matrix.org> original message\n\nThis is a reply"
    )

    with patch("mmrelay.matrix_utils.config", test_config), patch(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]
    ), patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]):
        # Mock the matrix client - use MagicMock to prevent coroutine warnings
        mock_matrix_client = MagicMock()
        with patch("mmrelay.matrix_utils.matrix_client", mock_matrix_client):
            # Run the function
            await on_room_message(mock_room, mock_event)

            # Assert that the message was queued
            mock_queue_message.assert_called_once()
            call_args = mock_queue_message.call_args[1]
            assert mock_event.body in call_args["text"]


@patch("mmrelay.matrix_utils.connect_meshtastic")
@patch("mmrelay.matrix_utils.queue_message")
@patch("mmrelay.matrix_utils.bot_start_time", 1234567880)
@patch("mmrelay.matrix_utils.get_message_map_by_matrix_event_id")
@patch("mmrelay.matrix_utils.get_user_display_name")
@patch("mmrelay.matrix_utils.isinstance")
async def test_on_room_message_reaction_enabled(
    mock_isinstance,
    mock_get_user_display_name,
    mock_get_message_map,
    mock_queue_message,
    mock_connect_meshtastic,
    mock_room,
    mock_event,
    test_config,
):
    # This is a reaction event
    """
    Test that a Matrix reaction event is processed and queued for Meshtastic relay when reaction interactions are enabled.

    Ensures that when a reaction event occurs and reaction interactions are enabled in the configuration, the corresponding reaction message is correctly constructed and queued for relay.
    """
    from nio import ReactionEvent

    mock_isinstance.side_effect = lambda event, event_type: event_type == ReactionEvent

    test_config["meshtastic"]["message_interactions"]["reactions"] = True
    mock_event.source = {
        "content": {
            "m.relates_to": {
                "event_id": "original_event_id",
                "key": "👍",
                "rel_type": "m.annotation",
            }
        }
    }
    mock_get_message_map.return_value = (
        "meshtastic_id",
        "!room:matrix.org",
        "original_text",
        "test_mesh",
    )

    # Create a proper async mock function
    async def mock_get_user_display_name_func(*args, **kwargs):
        """
        Asynchronously returns a fixed user display name string "user".

        Intended for use as a mock replacement in tests requiring an async display name retrieval function.
        """
        return "user"

    mock_get_user_display_name.side_effect = mock_get_user_display_name_func

    with patch("mmrelay.matrix_utils.config", test_config), patch(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]
    ), patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]):
        # Mock the matrix client - use MagicMock to prevent coroutine warnings
        mock_matrix_client = MagicMock()
        with patch("mmrelay.matrix_utils.matrix_client", mock_matrix_client):
            # Run the function
            await on_room_message(mock_room, mock_event)

            # Assert that the message was queued
            mock_queue_message.assert_called_once()
            call_args = mock_queue_message.call_args[1]
            assert "reacted 👍 to" in call_args["text"]


@patch("mmrelay.matrix_utils.connect_meshtastic")
@patch("mmrelay.matrix_utils.queue_message")
@patch("mmrelay.matrix_utils.bot_start_time", 1234567880)
@patch("mmrelay.matrix_utils.isinstance")
async def test_on_room_message_reaction_disabled(
    mock_isinstance,
    mock_queue_message,
    mock_connect_meshtastic,
    mock_room,
    mock_event,
    test_config,
):
    # This is a reaction event
    """
    Test that reaction events are not queued when reaction interactions are disabled in the configuration.
    """
    from nio import ReactionEvent

    mock_isinstance.side_effect = lambda event, event_type: event_type == ReactionEvent

    test_config["meshtastic"]["message_interactions"]["reactions"] = False
    mock_event.source = {
        "content": {
            "m.relates_to": {
                "event_id": "original_event_id",
                "key": "👍",
                "rel_type": "m.annotation",
            }
        }
    }

    with patch("mmrelay.matrix_utils.config", test_config), patch(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]
    ), patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]):
        # Mock the matrix client - use MagicMock to prevent coroutine warnings
        mock_matrix_client = MagicMock()
        with patch("mmrelay.matrix_utils.matrix_client", mock_matrix_client):
            # Run the function
            await on_room_message(mock_room, mock_event)

            # Assert that the message was not queued
            mock_queue_message.assert_not_called()


@patch("mmrelay.matrix_utils.connect_meshtastic")
@patch("mmrelay.matrix_utils.queue_message")
@patch("mmrelay.matrix_utils.bot_start_time", 1234567880)
async def test_on_room_message_unsupported_room(
    mock_queue_message, mock_connect_meshtastic, mock_room, mock_event, test_config
):
    """
    Test that messages from unsupported Matrix rooms are ignored.

    Verifies that when a message event originates from a Matrix room not listed in the configuration, it is not queued for Meshtastic relay.
    """
    mock_room.room_id = "!unsupported:matrix.org"
    with patch("mmrelay.matrix_utils.config", test_config), patch(
        "mmrelay.matrix_utils.matrix_rooms", test_config["matrix_rooms"]
    ), patch("mmrelay.matrix_utils.bot_user_id", test_config["matrix"]["bot_user_id"]):
        # Mock the matrix client - use MagicMock to prevent coroutine warnings
        mock_matrix_client = MagicMock()
        with patch("mmrelay.matrix_utils.matrix_client", mock_matrix_client):
            # Run the function
            await on_room_message(mock_room, mock_event)

            # Assert that the message was not queued
            mock_queue_message.assert_not_called()


# Matrix utility function tests - converted from unittest.TestCase to standalone pytest functions


@patch("mmrelay.matrix_utils.config", {})
def test_get_msgs_to_keep_config_default():
    """
    Test that the default message retention value is returned when no configuration is set.
    """
    result = _get_msgs_to_keep_config()
    assert result == 500


@patch("mmrelay.matrix_utils.config", {"db": {"msg_map": {"msgs_to_keep": 100}}})
def test_get_msgs_to_keep_config_legacy():
    """
    Test that the legacy configuration format correctly sets the message retention value.
    """
    result = _get_msgs_to_keep_config()
    assert result == 100


@patch("mmrelay.matrix_utils.config", {"database": {"msg_map": {"msgs_to_keep": 200}}})
def test_get_msgs_to_keep_config_new_format():
    """
    Test that the new configuration format correctly sets the message retention value.

    Verifies that `_get_msgs_to_keep_config()` returns the expected value when the configuration uses the new nested format for message retention.
    """
    result = _get_msgs_to_keep_config()
    assert result == 200


def test_create_mapping_info():
    """
    Tests that _create_mapping_info returns a dictionary with the correct message mapping information based on the provided parameters.
    """
    result = _create_mapping_info(
        matrix_event_id="$event123",
        room_id="!room:matrix.org",
        text="Hello world",
        meshnet="test_mesh",
        msgs_to_keep=100,
    )

    expected = {
        "matrix_event_id": "$event123",
        "room_id": "!room:matrix.org",
        "text": "Hello world",
        "meshnet": "test_mesh",
        "msgs_to_keep": 100,
    }
    assert result == expected


@patch("mmrelay.matrix_utils._get_msgs_to_keep_config", return_value=500)
def test_create_mapping_info_defaults(mock_get_msgs):
    """
    Test that _create_mapping_info returns a mapping dictionary with default values when optional parameters are not provided.
    """
    result = _create_mapping_info(
        matrix_event_id="$event123",
        room_id="!room:matrix.org",
        text="Hello world",
    )

    assert result["msgs_to_keep"] == 500
    assert result["meshnet"] is None


def test_get_interaction_settings_new_format():
    """
    Tests that interaction settings are correctly retrieved from a configuration using the new format.
    """
    config = {
        "meshtastic": {"message_interactions": {"reactions": True, "replies": False}}
    }

    result = get_interaction_settings(config)
    expected = {"reactions": True, "replies": False}
    assert result == expected


def test_get_interaction_settings_legacy_format():
    """
    Test that interaction settings are correctly parsed from a legacy configuration format.

    Verifies that the function returns the expected dictionary when only legacy keys are present in the configuration.
    """
    config = {"meshtastic": {"relay_reactions": True}}

    result = get_interaction_settings(config)
    expected = {"reactions": True, "replies": False}
    assert result == expected


def test_get_interaction_settings_defaults():
    """
    Test that default interaction settings are returned as disabled when no configuration is provided.
    """
    config = {}

    result = get_interaction_settings(config)
    expected = {"reactions": False, "replies": False}
    assert result == expected


def test_message_storage_enabled_true():
    """
    Test that message storage is enabled when either reactions or replies are enabled in the interaction settings.
    """
    interactions = {"reactions": True, "replies": False}
    assert message_storage_enabled(interactions)

    interactions = {"reactions": False, "replies": True}
    assert message_storage_enabled(interactions)

    interactions = {"reactions": True, "replies": True}
    assert message_storage_enabled(interactions)


def test_message_storage_enabled_false():
    """
    Test that message storage is disabled when both reactions and replies are disabled in the interaction settings.
    """
    interactions = {"reactions": False, "replies": False}
    assert not message_storage_enabled(interactions)


def test_add_truncated_vars():
    """
    Tests that truncated versions of a string are correctly added to a format dictionary with specific key suffixes.
    """
    format_vars = {}
    _add_truncated_vars(format_vars, "display", "Hello World")

    # Check that truncated variables are added
    assert format_vars["display1"] == "H"
    assert format_vars["display5"] == "Hello"
    assert format_vars["display10"] == "Hello Worl"
    assert format_vars["display20"] == "Hello World"


def test_add_truncated_vars_empty_text():
    """
    Test that _add_truncated_vars correctly handles empty string input by setting truncated variables to empty strings.
    """
    format_vars = {}
    _add_truncated_vars(format_vars, "display", "")

    # Should handle empty text gracefully
    assert format_vars["display1"] == ""
    assert format_vars["display5"] == ""


def test_add_truncated_vars_none_text():
    """
    Test that truncated variable keys are added with empty string values when the input text is None.
    """
    format_vars = {}
    _add_truncated_vars(format_vars, "display", None)

    # Should convert None to empty string
    assert format_vars["display1"] == ""
    assert format_vars["display5"] == ""


# Prefix formatting function tests - converted from unittest.TestCase to standalone pytest functions


def test_validate_prefix_format_valid():
    """
    Tests that a valid prefix format string with available variables passes validation without errors.
    """
    format_string = "{display5}[M]: "
    available_vars = {"display5": "Alice"}

    is_valid, error = validate_prefix_format(format_string, available_vars)
    assert is_valid
    assert error is None


def test_validate_prefix_format_invalid_key():
    """
    Tests that validate_prefix_format correctly identifies an invalid prefix format string containing a missing key.

    Verifies that the function returns False and provides an error message when the format string references a key not present in the available variables.
    """
    format_string = "{invalid_key}: "
    available_vars = {"display5": "Alice"}

    is_valid, error = validate_prefix_format(format_string, available_vars)
    assert not is_valid
    assert error is not None


def test_get_meshtastic_prefix_enabled():
    """
    Tests that the Meshtastic prefix is generated using the specified format when prefixing is enabled in the configuration.
    """
    config = {
        "meshtastic": {"prefix_enabled": True, "prefix_format": "{display5}[M]: "}
    }

    result = get_meshtastic_prefix(config, "Alice", "@alice:matrix.org")
    assert result == "Alice[M]: "


def test_get_meshtastic_prefix_disabled():
    """
    Tests that no Meshtastic prefix is generated when prefixing is disabled in the configuration.
    """
    config = {"meshtastic": {"prefix_enabled": False}}

    result = get_meshtastic_prefix(config, "Alice")
    assert result == ""


def test_get_meshtastic_prefix_custom_format():
    """
    Tests that a custom Meshtastic prefix format is applied correctly using the truncated display name.
    """
    config = {"meshtastic": {"prefix_enabled": True, "prefix_format": "[{display3}]: "}}

    result = get_meshtastic_prefix(config, "Alice")
    assert result == "[Ali]: "


def test_get_meshtastic_prefix_invalid_format():
    """
    Test that get_meshtastic_prefix falls back to the default format when given an invalid prefix format string.
    """
    config = {
        "meshtastic": {"prefix_enabled": True, "prefix_format": "{invalid_var}: "}
    }

    result = get_meshtastic_prefix(config, "Alice")
    assert result == "Alice[M]: "  # Default format


def test_get_matrix_prefix_enabled():
    """
    Tests that the Matrix prefix is generated correctly when prefixing is enabled and a custom format is provided.
    """
    config = {"matrix": {"prefix_enabled": True, "prefix_format": "[{long3}/{mesh}]: "}}

    result = get_matrix_prefix(config, "Alice", "A", "TestMesh")
    assert result == "[Ali/TestMesh]: "


def test_get_matrix_prefix_disabled():
    """
    Test that no Matrix prefix is generated when prefixing is disabled in the configuration.
    """
    config = {"matrix": {"prefix_enabled": False}}

    result = get_matrix_prefix(config, "Alice", "A", "TestMesh")
    assert result == ""


def test_get_matrix_prefix_default_format():
    """
    Tests that the default Matrix prefix format is used when no custom format is specified in the configuration.
    """
    config = {
        "matrix": {
            "prefix_enabled": True
            # No custom format specified
        }
    }

    result = get_matrix_prefix(config, "Alice", "A", "TestMesh")
    assert result == "[Alice/TestMesh]: "  # Default format


# Text processing function tests - converted from unittest.TestCase to standalone pytest functions


def test_truncate_message_under_limit():
    """
    Tests that a message shorter than the specified byte limit is not truncated by the truncate_message function.
    """
    text = "Hello world"
    result = truncate_message(text, max_bytes=50)
    assert result == "Hello world"


def test_truncate_message_over_limit():
    """
    Test that messages exceeding the specified byte limit are truncated without breaking character encoding.
    """
    text = "This is a very long message that exceeds the byte limit"
    result = truncate_message(text, max_bytes=20)
    assert len(result.encode("utf-8")) <= 20
    assert result.startswith("This is")


def test_truncate_message_unicode():
    """
    Tests that truncating a message containing Unicode characters does not split characters and respects the byte limit.
    """
    text = "Hello 🌍 world"
    result = truncate_message(text, max_bytes=10)
    # Should handle Unicode properly without breaking characters
    assert len(result.encode("utf-8")) <= 10


def test_strip_quoted_lines_with_quotes():
    """
    Tests that quoted lines (starting with '>') are removed from multi-line text, and remaining lines are joined with spaces.
    """
    text = "This is a reply\n> Original message\n> Another quoted line\nNew content"
    result = strip_quoted_lines(text)
    expected = "This is a reply New content"  # Joined with spaces
    assert result == expected


def test_strip_quoted_lines_no_quotes():
    """Test stripping quoted lines when no quotes exist."""
    text = "This is a normal message\nWith multiple lines"
    result = strip_quoted_lines(text)
    expected = "This is a normal message With multiple lines"  # Joined with spaces
    assert result == expected


def test_strip_quoted_lines_only_quotes():
    """
    Tests that stripping quoted lines from text returns an empty string when all lines are quoted.
    """
    text = "> First quoted line\n> Second quoted line"
    result = strip_quoted_lines(text)
    assert result == ""


def test_format_reply_message():
    """
    Tests that reply messages are formatted with a truncated display name and quoted lines are removed from the message body.
    """
    config = {}  # Using defaults
    result = format_reply_message(
        config, "Alice Smith", "This is a reply\n> Original message"
    )

    # Should include truncated display name and strip quoted lines
    assert result.startswith("Alice[M]: ")
    assert "> Original message" not in result
    assert "This is a reply" in result


def test_format_reply_message_remote_mesh_prefix():
    """Ensure remote mesh replies use the remote mesh prefix and raw payload."""

    config = {}
    result = format_reply_message(
        config,
        "MtP Relay",
        "[LoRa/Mt.P]: Test",
        longname="LoRa",
        shortname="Trak",
        meshnet_name="Mt.P",
        local_meshnet_name="Forx",
        mesh_text_override="Test",
    )

    assert result == "Trak/Mt.P: Test"


def test_format_reply_message_remote_without_longname():
    """Remote replies fall back to shortname when longname missing."""

    config = {}
    result = format_reply_message(
        config,
        "MtP Relay",
        "Tr/Mt.Peak: Hi",
        longname=None,
        shortname="Tr",
        meshnet_name="Mt.Peak",
        local_meshnet_name="Forx",
        mesh_text_override="Hi",
    )

    assert result == "Tr/Mt.P: Hi"


@pytest.mark.asyncio
async def test_join_matrix_room_by_id(monkeypatch):
    """Test joining a matrix room by ID."""
    from mmrelay import matrix_utils

    fake_client = MagicMock()
    fake_client.rooms = {}
    fake_client.join = AsyncMock(return_value=MagicMock(room_id="!room:matrix.org"))

    await join_matrix_room(fake_client, "!room:matrix.org")

    fake_client.join.assert_awaited_once_with("!room:matrix.org")


@pytest.mark.asyncio
async def test_join_matrix_room_already_joined(monkeypatch):
    """Test joining a matrix room when already a member."""
    from mmrelay import matrix_utils

    fake_client = MagicMock()
    fake_client.rooms = {"!room:matrix.org": MagicMock()}
    fake_client.join = AsyncMock()

    await join_matrix_room(fake_client, "!room:matrix.org")

    fake_client.join.assert_not_awaited()


# Bot command detection tests - refactored to use test class with fixtures for better maintainability


class TestBotCommand:
    """Test class for bot command detection functionality."""

    @pytest.fixture(autouse=True)
    def mock_bot_globals(self):
        """Fixture to mock bot user globals for all tests in this class."""
        with patch("mmrelay.matrix_utils.bot_user_id", "@bot:matrix.org"), patch(
            "mmrelay.matrix_utils.bot_user_name", "Bot"
        ):
            yield

    def test_direct_mention(self):
        """
        Tests that a message starting with the bot command triggers correct command detection.
        """
        mock_event = MagicMock()
        mock_event.body = "!help"
        mock_event.source = {"content": {"formatted_body": "!help"}}

        result = bot_command("help", mock_event)
        assert result

    def test_no_match(self):
        """
        Test that a non-command message does not trigger bot command detection.
        """
        mock_event = MagicMock()
        mock_event.body = "regular message"
        mock_event.source = {"content": {"formatted_body": "regular message"}}

        result = bot_command("help", mock_event)
        assert not result

    def test_case_insensitive(self):
        """
        Test that bot command detection is case-insensitive by verifying a command matches regardless of letter case.
        """
        mock_event = MagicMock()
        mock_event.body = "!HELP"
        mock_event.source = {"content": {"formatted_body": "!HELP"}}

        result = bot_command("HELP", mock_event)  # Command should match case
        assert result

    def test_with_args(self):
        """
        Test that the bot command is correctly detected when followed by additional arguments.
        """
        mock_event = MagicMock()
        mock_event.body = "!help me please"
        mock_event.source = {"content": {"formatted_body": "!help me please"}}

        result = bot_command("help", mock_event)
        assert result


# Async Matrix function tests - converted from unittest.TestCase to standalone pytest functions


@pytest.fixture
def matrix_config():
    """Test configuration for Matrix functions."""
    return {
        "matrix": {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org",
            "prefix_enabled": True,
        },
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
    }


async def test_connect_matrix_success(matrix_config):
    """
    Test that a Matrix client connects successfully using the provided configuration.

    Verifies that the client is instantiated, SSL context is created, and the client is authenticated and configured as expected.
    """
    with patch("mmrelay.matrix_utils.matrix_client", None), patch(
        "mmrelay.matrix_utils.AsyncClient"
    ) as mock_async_client, patch("mmrelay.matrix_utils.logger"), patch(
        "ssl.create_default_context"
    ) as mock_ssl_context:

        # Mock SSL context creation
        mock_ssl_context.return_value = MagicMock()

        # Mock the AsyncClient instance with proper async methods
        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}  # Add rooms attribute

        # Create proper async mock methods that return coroutines
        async def mock_whoami():
            """
            Asynchronous test helper that simulates a Matrix client's `whoami()` response.

            Returns:
                MagicMock: A mock object with `device_id` set to "test_device_id", matching the shape returned by an AsyncClient.whoami() call.
            """
            return MagicMock(device_id="test_device_id")

        async def mock_sync(*args, **kwargs):
            """
            Asynchronous stub that ignores all arguments and returns a MagicMock instance.

            Used in tests to mock async sync-like calls; can be awaited like a coroutine and will yield a MagicMock.
            Returns:
                MagicMock: A new MagicMock instance on each call.
            """
            return MagicMock()

        async def mock_get_displayname(*args, **kwargs):
            """
            Coroutine used in tests to simulate fetching a user's display name.

            Returns a MagicMock object with a `displayname` attribute set to "Test Bot".
            """
            return MagicMock(displayname="Test Bot")

        mock_client_instance.whoami = mock_whoami
        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_async_client.return_value = mock_client_instance

        result = await connect_matrix(matrix_config)

        # Verify client was created and configured
        mock_async_client.assert_called_once()
        assert result == mock_client_instance
        # Note: whoami() is no longer called in the new E2EE implementation


async def test_connect_matrix_without_credentials(matrix_config):
    """
    Test that `connect_matrix` returns the Matrix client successfully when using legacy config without credentials.json.
    """
    with patch("mmrelay.matrix_utils.matrix_client", None), patch(
        "mmrelay.matrix_utils.AsyncClient"
    ) as mock_async_client, patch("mmrelay.matrix_utils.logger"), patch(
        "ssl.create_default_context"
    ) as mock_ssl_context:

        # Mock SSL context creation
        mock_ssl_context.return_value = MagicMock()

        # Mock the AsyncClient instance with proper async methods
        mock_client_instance = MagicMock()
        mock_client_instance.rooms = {}  # Add missing rooms attribute
        mock_client_instance.device_id = None  # Set device_id to None for legacy config

        # Create proper async mock methods that return coroutines
        async def mock_sync(*args, **kwargs):
            """
            Asynchronous stub that ignores all arguments and returns a MagicMock instance.

            Used in tests to mock async sync-like calls; can be awaited like a coroutine and will yield a MagicMock.
            Returns:
                MagicMock: A new MagicMock instance on each call.
            """
            return MagicMock()

        async def mock_get_displayname(*args, **kwargs):
            """
            Coroutine used in tests to simulate fetching a user's display name.

            Returns a MagicMock object with a `displayname` attribute set to "Test Bot".
            """
            return MagicMock(displayname="Test Bot")

        mock_client_instance.sync = mock_sync
        mock_client_instance.get_displayname = mock_get_displayname
        mock_async_client.return_value = mock_client_instance

        result = await connect_matrix(matrix_config)

        # Should return client successfully
        assert result == mock_client_instance
        # Note: device_id remains None for legacy config without E2EE


@patch("mmrelay.matrix_utils.matrix_client")
@patch("mmrelay.matrix_utils.logger")
async def test_join_matrix_room_by_id(mock_logger, mock_matrix_client):
    """
    Test that joining a Matrix room by its room ID calls the client's join method with the correct argument.
    """
    # Use MagicMock to prevent coroutine warnings
    mock_matrix_client.join = AsyncMock()

    await join_matrix_room(mock_matrix_client, "!room:matrix.org")

    mock_matrix_client.join.assert_called_once_with("!room:matrix.org")


@patch("mmrelay.matrix_utils.matrix_client")
@patch("mmrelay.matrix_utils.logger")
async def test_join_matrix_room_already_joined(mock_logger, mock_matrix_client):
    """Test that join_matrix_room does nothing if already in the room."""
    mock_matrix_client.rooms = {"!room:matrix.org": MagicMock()}

    await join_matrix_room(mock_matrix_client, "!room:matrix.org")

    mock_matrix_client.join.assert_not_called()
    mock_logger.debug.assert_called_with(
        "Bot is already in room '!room:matrix.org', no action needed."
    )
