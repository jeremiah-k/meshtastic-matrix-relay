from unittest.mock import patch

import pytest

from mmrelay.matrix_utils import (
    _add_truncated_vars,
    _create_mapping_info,
    _escape_leading_prefix_for_markdown,
    _get_valid_device_id,
    _normalize_bot_user_id,
    format_reply_message,
    get_interaction_settings,
    get_matrix_prefix,
    get_meshtastic_prefix,
    message_storage_enabled,
    strip_quoted_lines,
    truncate_message,
    validate_prefix_format,
)

# Configuration & Mapping Tests


def test_create_mapping_info_defaults():
    """
    Test that _create_mapping_info returns a mapping dictionary with default values when optional parameters are not provided.
    """
    with patch("mmrelay.matrix_utils._get_msgs_to_keep_config", return_value=500):
        result = _create_mapping_info(
            matrix_event_id="$event123",
            room_id="!room:matrix.org",
            text="Hello world",
        )

    assert result is not None
    assert result["msgs_to_keep"] == 500
    assert result["meshnet"] is None


@pytest.mark.parametrize(
    "event_id, room_id, text",
    [
        pytest.param(None, "!room:matrix.org", "Hello", id="none_event_id"),
        pytest.param("$event123", "", "Hello", id="empty_room_id"),
        pytest.param("$event123", "!room:matrix.org", None, id="none_text"),
        pytest.param("$event123", "!room:matrix.org", "", id="empty_text"),
    ],
)
def test_create_mapping_info_none_values(event_id, room_id, text):
    """
    Test that _create_mapping_info returns None when required parameters are None or empty.
    """
    result = _create_mapping_info(event_id, room_id, text)
    assert result is None


def test_create_mapping_info_with_quoted_text():
    """
    Test that _create_mapping_info strips quoted lines from text.
    """
    text = "This is a reply\n> Original message\n> Another quote\nNew content"

    with patch("mmrelay.matrix_utils._get_msgs_to_keep_config", return_value=100):
        result = _create_mapping_info(
            matrix_event_id="$event123",
            room_id="!room:matrix.org",
            text=text,
            meshnet="test_mesh",
            msgs_to_keep=100,
        )

    expected = {
        "matrix_event_id": "$event123",
        "room_id": "!room:matrix.org",
        "text": "This is a reply New content",  # Quotes stripped
        "meshnet": "test_mesh",
        "msgs_to_keep": 100,
    }
    assert result == expected


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


def test_get_interaction_settings_none_config():
    """Test interaction settings when config is None."""
    result = get_interaction_settings(None)
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


# Formatting Tests


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


@pytest.mark.parametrize(
    "name_part",
    [
        "Test_Node",
        "_Name_",
        "__Name__",
        "*Name*",
        "*_Name_*",
        "Name_with_*_mix",
        "Name~tilde",
        "Name`code`",
        r"Name\with\slash",
        "User[test]",
    ],
)
def test_escape_leading_prefix_for_markdown_with_markdown_chars(name_part):
    """
    Prefix-style messages containing markdown characters should render intact instead of being stripped or formatted.
    """
    original = f"[{name_part}/Mesh]: hello world"
    safe, escaped = _escape_leading_prefix_for_markdown(original)

    escape_map = {
        "\\": "\\\\",
        "*": "\\*",
        "_": "\\_",
        "`": "\\`",
        "~": "\\~",
        "[": "\\[",
        "]": "\\]",
    }
    escaped_name = "".join(escape_map.get(ch, ch) for ch in name_part)
    expected_prefix = f"\\[{escaped_name}/Mesh]:"
    assert safe.startswith(expected_prefix)
    assert safe.endswith("hello world")
    assert escaped


def test_escape_leading_prefix_for_markdown_non_prefix():
    """Non-prefix strings should remain unchanged."""
    unchanged = "No prefix here"
    processed, escaped = _escape_leading_prefix_for_markdown(unchanged)
    assert processed == unchanged
    assert escaped is False


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


def test_format_reply_message_remote_strips_prefix_and_uses_override(monkeypatch):
    """Remote replies strip matching prefixes before rebuilding the reply text."""
    config = {}
    monkeypatch.setattr(
        "mmrelay.matrix_utils.get_matrix_prefix", lambda *_args, **_kwargs: "PREFIX"
    )

    result = format_reply_message(
        config,
        "Alice",
        "Ignored",
        longname="Alice",
        shortname="Al",
        meshnet_name="RemoteMesh",
        local_meshnet_name="LocalMesh",
        mesh_text_override="PREFIX",
    )

    assert result.startswith("Al/Remo:")
    assert "PREFIX" in result


# Utils Tests


def test_normalize_bot_user_id_already_full_mxid():
    """Test that _normalize_bot_user_id returns full MXID as-is."""

    homeserver = "https://example.com"
    bot_user_id = "@relaybot:example.com"

    result = _normalize_bot_user_id(homeserver, bot_user_id)
    assert result == "@relaybot:example.com"


def test_normalize_bot_user_id_ipv6_homeserver():
    """Test that _normalize_bot_user_id handles IPv6 homeserver URLs correctly."""

    homeserver = "https://[2001:db8::1]:8448"
    bot_user_id = "relaybot"

    result = _normalize_bot_user_id(homeserver, bot_user_id)
    assert result == "@relaybot:[2001:db8::1]"


def test_normalize_bot_user_id_full_mxid_with_port():
    """Test that _normalize_bot_user_id strips the port from a full MXID."""

    homeserver = "https://example.com:8448"
    bot_user_id = "@bot:example.com:8448"

    result = _normalize_bot_user_id(homeserver, bot_user_id)
    assert result == "@bot:example.com"


def test_normalize_bot_user_id_with_at_prefix():
    """Test that _normalize_bot_user_id adds homeserver to @-prefixed username."""

    homeserver = "https://example.com"
    bot_user_id = "@relaybot"

    result = _normalize_bot_user_id(homeserver, bot_user_id)
    assert result == "@relaybot:example.com"


def test_normalize_bot_user_id_without_at_prefix():
    """Test that _normalize_bot_user_id adds @ and homeserver to plain username."""

    homeserver = "https://example.com"
    bot_user_id = "relaybot"

    result = _normalize_bot_user_id(homeserver, bot_user_id)
    assert result == "@relaybot:example.com"


def test_normalize_bot_user_id_with_complex_homeserver():
    """Test that _normalize_bot_user_id handles complex homeserver URLs."""

    homeserver = "https://matrix.example.com:8448"
    bot_user_id = "relaybot"

    result = _normalize_bot_user_id(homeserver, bot_user_id)
    assert result == "@relaybot:matrix.example.com"


def test_normalize_bot_user_id_empty_input():
    """Test that _normalize_bot_user_id handles empty input gracefully."""

    homeserver = "https://example.com"
    bot_user_id = ""

    result = _normalize_bot_user_id(homeserver, bot_user_id)
    assert result == ""


def test_normalize_bot_user_id_none_input():
    """Test that _normalize_bot_user_id handles None input gracefully."""

    homeserver = "https://example.com"
    bot_user_id = None

    result = _normalize_bot_user_id(homeserver, bot_user_id)
    assert result is None


def test_normalize_bot_user_id_trailing_colon():
    """Test that _normalize_bot_user_id handles trailing colons gracefully."""

    homeserver = "https://example.com"
    bot_user_id = "@relaybot:"

    result = _normalize_bot_user_id(homeserver, bot_user_id)
    assert result == "@relaybot:example.com"


def test_get_valid_device_id_valid_string():
    """
    Test that _get_valid_device_id returns stripped string for valid input.
    """
    device_id = "  test_device_id  "

    result = _get_valid_device_id(device_id)

    assert result == "test_device_id"


def test_get_valid_device_id_empty_string():
    """
    Test that _get_valid_device_id returns None for empty string.
    """
    device_id = "   "

    result = _get_valid_device_id(device_id)

    assert result is None


def test_get_valid_device_id_non_string():
    """
    Test that _get_valid_device_id returns None for non-string input.
    """
    result = _get_valid_device_id(123)
    assert result is None

    result = _get_valid_device_id(None)
    assert result is None

    result = _get_valid_device_id([])
    assert result is None
