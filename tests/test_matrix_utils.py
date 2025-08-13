from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

# Testing framework: pytest with pytest-asyncio for async tests

# Import module under test
# The project appears to use 'mmrelay.matrix_utils' as the source module.
# If the module path differs, adjust this import accordingly.
import importlib

matrix_utils = importlib.import_module("mmrelay.matrix_utils")


# ----------------------------
# Helpers and fixtures
# ----------------------------

@pytest.fixture(autouse=True)
def reset_globals(monkeypatch):
    """
    Reset or stub global state in matrix_utils to avoid cross-test interference.
    """
    # Provide a minimal default config structure used by many functions
    default_config = {
        "database": {"msg_map": {"msgs_to_keep": matrix_utils.DEFAULT_MSGS_TO_KEEP}},
        "meshtastic": {
            "prefix_enabled": True,
            "prefix_format": matrix_utils.DEFAULT_MESHTASTIC_PREFIX,
            "meshnet_name": "localmesh",
            "message_interactions": {"reactions": False, "replies": False},
            "broadcast_enabled": True,
            "detection_sensor": matrix_utils.DEFAULT_DETECTION_SENSOR,
        },
        "matrix": {
            "bot_user_id": "@bot:server",
        },
        "matrix_rooms": [],
        matrix_utils.CONFIG_SECTION_MATRIX: {
            matrix_utils.CONFIG_KEY_HOMESERVER: "https://example.org",
            matrix_utils.CONFIG_KEY_ACCESS_TOKEN: "abc123",
            "prefix_enabled": True,
            "prefix_format": matrix_utils.DEFAULT_MATRIX_PREFIX,
        },
    }
    monkeypatch.setattr(matrix_utils, "config", default_config, raising=True)
    monkeypatch.setattr(matrix_utils, "matrix_client", None, raising=True)
    monkeypatch.setattr(matrix_utils, "matrix_homeserver", None, raising=True)
    monkeypatch.setattr(matrix_utils, "matrix_rooms", [], raising=True)
    monkeypatch.setattr(matrix_utils, "matrix_access_token", None, raising=True)
    monkeypatch.setattr(matrix_utils, "bot_user_id", "@bot:server", raising=True)
    monkeypatch.setattr(matrix_utils, "bot_user_name", "Relay Bot", raising=True)


@pytest.fixture
def dummy_room():
    class DummyRoom:
        def __init__(self):
            self._names = {}
            self.room_id = "!room:server"

        def user_name(self, sender):
            return self._names.get(sender)

        def set_room_name(self, user, name):
            self._names[user] = name

    return DummyRoom()


@pytest.fixture
def dummy_event():
    """
    Minimal structure resembling RoomMessageText/Notice/Emote with needed attributes.
    """
    class DummyEvent:
        def __init__(self, sender="@alice:server", body="hello", ts=10_000_000_000):
            self.sender = sender
            self.body = body
            self.server_timestamp = ts
            self.event_id = "$evt"
            self.source = {
                "content": {
                    "body": body,
                    "formatted_body": body,
                    "msgtype": "m.text",
                }
            }

    return DummyEvent()


# ----------------------------
# Tests for _get_msgs_to_keep_config
# ----------------------------

def test_get_msgs_to_keep_config_default_when_no_config(monkeypatch):
    monkeypatch.setattr(matrix_utils, "config", None, raising=True)
    assert matrix_utils._get_msgs_to_keep_config() == matrix_utils.DEFAULT_MSGS_TO_KEEP


def test_get_msgs_to_keep_config_from_database(monkeypatch):
    cfg = {
        "database": {"msg_map": {"msgs_to_keep": 42}},
        "db": {},
    }
    monkeypatch.setattr(matrix_utils, "config", cfg, raising=True)
    assert matrix_utils._get_msgs_to_keep_config() == 42


def test_get_msgs_to_keep_config_legacy_db_with_warning(monkeypatch):
    cfg = {"database": {}, "db": {"msg_map": {"msgs_to_keep": 17}}}
    monkeypatch.setattr(matrix_utils, "config", cfg, raising=True)
    with patch.object(matrix_utils.logger, "warning") as warn:
        assert matrix_utils._get_msgs_to_keep_config() == 17
        warn.assert_called()


def test_get_msgs_to_keep_config_fallback_default(monkeypatch):
    cfg = {"database": {"msg_map": {}}, "db": {}}
    monkeypatch.setattr(matrix_utils, "config", cfg, raising=True)
    assert matrix_utils._get_msgs_to_keep_config() == matrix_utils.DEFAULT_MSGS_TO_KEEP


# ----------------------------
# Tests for _create_mapping_info
# ----------------------------

def test_create_mapping_info_requires_fields():
    assert matrix_utils._create_mapping_info("", "room", "text") is None
    assert matrix_utils._create_mapping_info("evt", "", "text") is None
    assert matrix_utils._create_mapping_info("evt", "room", "") is None
    assert matrix_utils._create_mapping_info(None, "room", "text") is None


def test_create_mapping_info_uses_strip_and_defaults(monkeypatch):
    monkeypatch.setattr(matrix_utils, "_get_msgs_to_keep_config", lambda: 99, raising=True)
    info = matrix_utils._create_mapping_info(
        "evt1", "!room", ">quoted line\nactual content", meshnet="m1", msgs_to_keep=None
    )
    assert info["matrix_event_id"] == "evt1"
    assert info["room_id"] == "!room"
    assert info["meshnet"] == "m1"
    # strip_quoted_lines should remove quoted line
    assert info["text"] == "actual content"
    assert info["msgs_to_keep"] == 99


def test_create_mapping_info_uses_provided_msgs_to_keep():
    info = matrix_utils._create_mapping_info("evt2", "!room", "hello", msgs_to_keep=5)
    assert info["msgs_to_keep"] == 5


# ----------------------------
# Tests for get_interaction_settings and message_storage_enabled
# ----------------------------

def test_get_interaction_settings_none_config_defaults():
    assert matrix_utils.get_interaction_settings(None) == {"reactions": False, "replies": False}
    assert matrix_utils.message_storage_enabled({"reactions": False, "replies": False}) is False


def test_get_interaction_settings_structured():
    cfg = {"meshtastic": {"message_interactions": {"reactions": True, "replies": False}}}
    res = matrix_utils.get_interaction_settings(cfg)
    assert res == {"reactions": True, "replies": False}
    assert matrix_utils.message_storage_enabled(res) is True


def test_get_interaction_settings_legacy(monkeypatch):
    cfg = {"meshtastic": {"relay_reactions": True}}
    with patch.object(matrix_utils.logger, "warning") as warn:
        res = matrix_utils.get_interaction_settings(cfg)
        warn.assert_called()
    assert res == {"reactions": True, "replies": False}


# ----------------------------
# Tests for _add_truncated_vars
# ----------------------------

def test_add_truncated_vars_handles_none_and_builds_all():
    d = {}
    matrix_utils._add_truncated_vars(d, "display", None)
    # Should populate display1..display20 keys with empty strings
    for i in range(1, 21):
        assert d[f"display{i}"] == ""


def test_add_truncated_vars_truncates_correctly():
    d = {}
    matrix_utils._add_truncated_vars(d, "name", "ABCDEFG")
    assert d["name1"] == "A"
    assert d["name3"] == "ABC"
    assert d["name7"] == "ABCDEFG"
    assert d["name8"] == "ABCDEFG"  # slicing beyond string length keeps full string


# ----------------------------
# Tests for validate_prefix_format
# ----------------------------

def test_validate_prefix_format_valid():
    ok, err = matrix_utils.validate_prefix_format(
        "Hello {display} {display5} {user} {username} {server}",
        {"display": "Alice", "display5": "Alice", "user": "@a:s", "username": "a", "server": "s"},
    )
    assert ok is True
    assert err is None


def test_validate_prefix_format_invalid_key():
    ok, err = matrix_utils.validate_prefix_format("{missing}", {})
    assert ok is False
    assert isinstance(err, str)
    assert "missing" in err


# ----------------------------
# Tests for get_meshtastic_prefix
# ----------------------------

def test_get_meshtastic_prefix_disabled(monkeypatch):
    cfg = {"meshtastic": {"prefix_enabled": False}}
    assert matrix_utils.get_meshtastic_prefix(cfg, "Alice", "@alice:server") == ""


def test_get_meshtastic_prefix_default(monkeypatch):
    cfg = {"meshtastic": {"prefix_enabled": True}}
    # DEFAULT_MESHTASTIC_PREFIX uses {display5}
    res = matrix_utils.get_meshtastic_prefix(cfg, "Alice Smith", "@alice:server")
    assert res == matrix_utils.DEFAULT_MESHTASTIC_PREFIX.format(display5="Alice")


def test_get_meshtastic_prefix_custom_with_username_and_server():
    cfg = {"meshtastic": {"prefix_enabled": True, "prefix_format": "{username}@{server}: "}}
    res = matrix_utils.get_meshtastic_prefix(cfg, "Bob", "@bob:example.com")
    assert res == "bob@example.com: "


def test_get_meshtastic_prefix_handles_invalid_fallback(monkeypatch):
    cfg = {"meshtastic": {"prefix_enabled": True, "prefix_format": "{unknown}: "}}
    with patch.object(matrix_utils.logger, "warning") as warn:
        res = matrix_utils.get_meshtastic_prefix(cfg, "Carol")
        warn.assert_called()
    assert res == matrix_utils.DEFAULT_MESHTASTIC_PREFIX.format(display5="Carol"[:5])


# ----------------------------
# Tests for get_matrix_prefix
# ----------------------------

def test_get_matrix_prefix_disabled(monkeypatch):
    monkeypatch.setattr(
        matrix_utils,
        "config",
        {matrix_utils.CONFIG_SECTION_MATRIX: {"prefix_enabled": False}},
        raising=True,
    )
    assert matrix_utils.get_matrix_prefix(matrix_utils.config, "Long", "Sho", "Mesh") == ""


def test_get_matrix_prefix_default(monkeypatch):
    monkeypatch.setattr(
        matrix_utils,
        "config",
        {matrix_utils.CONFIG_SECTION_MATRIX: {"prefix_enabled": True}},
        raising=True,
    )
    res = matrix_utils.get_matrix_prefix(matrix_utils.config, "LongName", "LNG", "Grid")
    assert res == matrix_utils.DEFAULT_MATRIX_PREFIX.format(long="LongName", mesh="Grid")


def test_get_matrix_prefix_custom_and_truncation(monkeypatch):
    monkeypatch.setattr(
        matrix_utils,
        "config",
        {
            matrix_utils.CONFIG_SECTION_MATRIX: {
                "prefix_enabled": True,
                "prefix_format": "[{long4}/{mesh3}] ",
            }
        },
        raising=True,
    )
    res = matrix_utils.get_matrix_prefix(matrix_utils.config, "ALONGNAME", "A", "NETNAME")
    assert res == "[ALON/NET] "


def test_get_matrix_prefix_invalid_fallback(monkeypatch):
    monkeypatch.setattr(
        matrix_utils,
        "config",
        {
            matrix_utils.CONFIG_SECTION_MATRIX: {
                "prefix_enabled": True,
                "prefix_format": "{badvar} ",
            }
        },
        raising=True,
    )
    with patch.object(matrix_utils.logger, "warning") as warn:
        res = matrix_utils.get_matrix_prefix(matrix_utils.config, None, None, None)
        warn.assert_called()
    assert res == matrix_utils.DEFAULT_MATRIX_PREFIX.format(long="", mesh="")


# ----------------------------
# Tests for truncate_message and strip_quoted_lines
# ----------------------------

def test_truncate_message_byte_safe():
    text = "😀" * 500  # multibyte
    res = matrix_utils.truncate_message(text, max_bytes=10)  # not divisible cleanly
    # Result must decode properly and be <= 10 bytes when utf-8 encoded
    assert isinstance(res, str)
    assert len(res.encode("utf-8")) <= 10


def test_strip_quoted_lines_basic():
    text = "> quoted\n\n  > quoted 2\nkeep\n  keep2"
    res = matrix_utils.strip_quoted_lines(text)
    assert res == "keep keep2"


# ----------------------------
# Tests for bot_command
# ----------------------------

@pytest.mark.parametrize(
    "body,formatted,command,expect",
    [
        ("!help", "", "help", True),
        ("!help arg", "", "help", True),
        ("No command here", "", "help", False),
        ("@bot:server, !help", "", "help", True),
        ("#general: !help", "", "help", True),
    ],
)
def test_bot_command_variants(body, formatted, command, expect, monkeypatch):
    event = SimpleNamespace()
    event.body = body
    event.source = {"content": {"formatted_body": formatted}}
    monkeypatch.setattr(matrix_utils, "bot_user_id", "@bot:server", raising=True)
    monkeypatch.setattr(matrix_utils, "bot_user_name", "Relay Bot", raising=True)
    assert matrix_utils.bot_command(command, event) is expect


# ----------------------------
# Tests for get_user_display_name
# ----------------------------

@pytest.mark.asyncio
async def test_get_user_display_name_prefers_room_name(monkeypatch, dummy_room):
    event = SimpleNamespace(sender="@alice:server")
    dummy_room.set_room_name("@alice:server", "RoomAlice")
    res = await matrix_utils.get_user_display_name(dummy_room, event)
    assert res == "RoomAlice"


@pytest.mark.asyncio
async def test_get_user_display_name_falls_back_to_global_display(monkeypatch, dummy_room):
    event = SimpleNamespace(sender="@alice:server")
    mock_client = SimpleNamespace(
        get_displayname=AsyncMock(return_value=SimpleNamespace(displayname="GlobalAlice"))
    )
    monkeypatch.setattr(matrix_utils, "matrix_client", mock_client, raising=True)
    res = await matrix_utils.get_user_display_name(dummy_room, event)
    assert res == "GlobalAlice"


# ----------------------------
# Tests for format_reply_message
# ----------------------------

def test_format_reply_message_strips_and_truncates(monkeypatch):
    cfg = {"meshtastic": {"prefix_enabled": True, "prefix_format": "{display5}[M]: "}}
    # 227 bytes default; build a long text with quoted lines and ensure truncation occurs
    long_text = "> quoted line\n" + ("x" * 500)
    res = matrix_utils.format_reply_message(cfg, "Alice Smith", long_text)
    # Should start with prefix using first 5 chars
    assert res.startswith("Alice[M]: ")
    # Should not contain quoted content
    assert "> quoted" not in res
    # Byte length should be <= 227
    assert len(res.encode("utf-8")) <= 227


# ----------------------------
# Tests for handle_matrix_reply (core logic with mocking)
# ----------------------------

@pytest.mark.asyncio
async def test_handle_matrix_reply_not_found_returns_false(monkeypatch, dummy_room, dummy_event):
    monkeypatch.setattr(matrix_utils, "get_message_map_by_matrix_event_id", MagicMock(return_value=None))
    res = await matrix_utils.handle_matrix_reply(
        dummy_room, dummy_event, "$orig", "text", {"meshtastic_channel": 0}, True, "localmesh", matrix_utils.config
    )
    assert res is False


@pytest.mark.asyncio
async def test_handle_matrix_reply_found_relays(monkeypatch, dummy_room, dummy_event):
    # orig tuple: (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
    monkeypatch.setattr(
        matrix_utils, "get_message_map_by_matrix_event_id",
        MagicMock(return_value=("m123", "!room", "orig text", "mesh"))
    )
    # Stub user display and send logic
    monkeypatch.setattr(matrix_utils, "get_user_display_name", AsyncMock(return_value="RoomAlice"))
    monkeypatch.setattr(matrix_utils, "send_reply_to_meshtastic", AsyncMock())
    res = await matrix_utils.handle_matrix_reply(
        dummy_room, dummy_event, "$orig", "reply body", {"meshtastic_channel": 1}, True, "localmesh", matrix_utils.config
    )
    assert res is True
    matrix_utils.send_reply_to_meshtastic.assert_awaited()


# ----------------------------
# Tests for send_reply_to_meshtastic queueing paths
# ----------------------------

@pytest.mark.asyncio
async def test_send_reply_to_meshtastic_structured_with_mapping(monkeypatch, dummy_room, dummy_event):
    # Prepare config to enable storage and broadcast
    cfg = {
        "database": {"msg_map": {"msgs_to_keep": 3}},
        "meshtastic": {
            "broadcast_enabled": True,
            "meshnet_name": "localmesh",
            "message_interactions": {"reactions": True, "replies": True},
            "prefix_enabled": True,
            "prefix_format": "{display5}[M]: ",
        },
        "matrix": {"bot_user_id": "@bot:server"},
        "matrix_rooms": [],
        matrix_utils.CONFIG_SECTION_MATRIX: {
            matrix_utils.CONFIG_KEY_HOMESERVER: "https://example.org",
            matrix_utils.CONFIG_KEY_ACCESS_TOKEN: "abc123",
        },
    }
    monkeypatch.setattr(matrix_utils, "config", cfg, raising=True)

    # Stub meshtastic interface and queue
    fake_iface = SimpleNamespace()
    monkeypatch.setattr(matrix_utils, "connect_meshtastic", lambda: fake_iface, raising=True)
    fake_queue = MagicMock()
    fake_queue.get_queue_size = MagicMock(return_value=1)
    monkeypatch.setattr(matrix_utils, "get_message_queue", lambda: fake_queue, raising=True)

    # queue_message stub should return True to simulate success
    monkeypatch.setattr(matrix_utils, "queue_message", MagicMock(return_value=True), raising=True)

    # Execute with reply_id to go through structured reply branch
    await matrix_utils.send_reply_to_meshtastic(
        reply_message="Hi there",
        full_display_name="Alice",
        room_config={"meshtastic_channel": 0},
        room=dummy_room,
        event=dummy_event,
        text="> quoted\nreal",
        storage_enabled=True,
        local_meshnet_name="localmesh",
        reply_id="m123",
    )

    # queue_message should be called with sendTextReply and mapping_info populated
    called_args, called_kwargs = matrix_utils.queue_message.call_args
    # sendTextReply is passed as the first argument
    assert called_args[0].__name__ == "sendTextReply"
    assert called_kwargs["reply_id"] == "m123"
    assert "mapping_info" in called_kwargs
    assert called_kwargs["mapping_info"]["matrix_event_id"] == dummy_event.event_id


@pytest.mark.asyncio
async def test_send_reply_to_meshtastic_regular_message_when_no_reply_id(monkeypatch, dummy_room, dummy_event):
    cfg = {"meshtastic": {"broadcast_enabled": True, "meshnet_name": "localmesh"}}
    monkeypatch.setattr(matrix_utils, "config", cfg, raising=True)

    fake_iface = SimpleNamespace(sendText=MagicMock())
    monkeypatch.setattr(matrix_utils, "connect_meshtastic", lambda: fake_iface, raising=True)
    fake_queue = MagicMock()
    fake_queue.get_queue_size = MagicMock(return_value=2)
    monkeypatch.setattr(matrix_utils, "get_message_queue", lambda: fake_queue, raising=True)
    monkeypatch.setattr(matrix_utils, "queue_message", MagicMock(return_value=True), raising=True)

    await matrix_utils.send_reply_to_meshtastic(
        reply_message="Hello", full_display_name="Bob",
        room_config={"meshtastic_channel": 2},
        room=dummy_room, event=dummy_event, text="text",
        storage_enabled=False, local_meshnet_name="localmesh",
        reply_id=None
    )

    # Should call queue_message with meshtastic_interface.sendText
    called_args, called_kwargs = matrix_utils.queue_message.call_args
    assert called_kwargs["description"].startswith("Reply from Bob")
    # mapping_info should be None when storage disabled
    assert "mapping_info" in called_kwargs and called_kwargs["mapping_info"] is None


# ----------------------------
# Tests for connect_matrix failure on missing config and SSL errors
# ----------------------------

@pytest.mark.asyncio
async def test_connect_matrix_no_config_returns_none(monkeypatch):
    monkeypatch.setattr(matrix_utils, "config", None, raising=True)
    res = await matrix_utils.connect_matrix()
    assert res is None


@pytest.mark.asyncio
async def test_connect_matrix_ssl_context_creation_error(monkeypatch):
    # Provide minimal config
    cfg = {
        matrix_utils.CONFIG_SECTION_MATRIX: {
            matrix_utils.CONFIG_KEY_HOMESERVER: "https://example.org",
            matrix_utils.CONFIG_KEY_ACCESS_TOKEN: "abc123",
        },
        "matrix": {"bot_user_id": "@bot:server"},
        "matrix_rooms": [],
    }
    monkeypatch.setattr(matrix_utils, "config", cfg, raising=True)

    # Force ssl.create_default_context to raise
    with patch("mmrelay.matrix_utils.ssl.create_default_context", side_effect=RuntimeError("boom")), pytest.raises(ConnectionError):
        await matrix_utils.connect_matrix()

# Note:
# Heavier integration behavior of matrix_relay and on_room_message is intentionally
# not fully executed here due to external IO and complex interactions; instead,
# we validated the critical helper logic, configuration handling, mapping preparation,
# and branching via unit tests with mocks to ensure high coverage of pure logic and
# side-effect triggers.