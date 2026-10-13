#!/usr/bin/env python3
"""
Tests for the meshtastic_cli Matrix plugin.

Covers configuration accessors, the admin-room gate, power-level
authorization, help output, command execution wiring, busy-lock rejection,
and reply formatting for executor results and refusals.
"""

import asyncio
import contextlib
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nio import RoomGetStateResponse

from mmrelay.matrix_utils import _dispatch_unmapped_room_message
from mmrelay.meshtastic_cli_executor import CliCommandError, CliCommandResult
from mmrelay.plugins.base_plugin import BasePlugin
from mmrelay.plugins.meshtastic_cli_plugin import Plugin, _join_execution

ADMIN_ROOM = "!admin:matrix.org"
ADMIN_SENDER = "@alice:matrix.org"
REMOTE_NODE_ID = "!a4bf9d0c"


@pytest.fixture(autouse=True)
def matrix_state_client():
    """Model authoritative room state separately from nio's cached levels."""
    client = MagicMock()
    client.room_get_state = AsyncMock(
        return_value=RoomGetStateResponse(
            events=[
                {
                    "type": "m.room.power_levels",
                    "state_key": "",
                    "content": {"users": {ADMIN_SENDER: 50}},
                }
            ],
            room_id=ADMIN_ROOM,
        )
    )
    with patch("mmrelay.matrix_utils.matrix_client", client):
        yield client


def _plugin(**config: Any) -> Plugin:
    plugin = Plugin()
    plugin.config = {"active": True, **config}
    plugin.send_matrix_message = AsyncMock()  # type: ignore[method-assign]
    plugin.send_matrix_reaction = AsyncMock()  # type: ignore[method-assign]
    return plugin


def _room(
    room_id: str = ADMIN_ROOM,
    users: dict[str, int] | None = None,
    users_default: int = 0,
) -> MagicMock:
    room = MagicMock()
    room.room_id = room_id
    room.power_levels = SimpleNamespace(
        users=users if users is not None else {},
        defaults=SimpleNamespace(users_default=users_default),
    )
    return room


def _event(body: str, sender: str = ADMIN_SENDER) -> MagicMock:
    event = MagicMock()
    event.sender = sender
    event.body = body
    event.event_id = "$event:matrix.org"
    event.source = {"content": {"body": body}}
    return event


async def _call(plugin: Plugin, room: Any, body: str) -> bool:
    return await plugin.handle_room_message(room, _event(body), "")


def _reactions(plugin: Plugin) -> list[str]:
    return [call.args[2] for call in plugin.send_matrix_reaction.call_args_list]


def _last_message(plugin: Plugin) -> str:
    return plugin.send_matrix_message.call_args.args[1]


# --- configuration ---------------------------------------------------------


def test_defaults() -> None:
    plugin = _plugin()
    assert plugin.get_matrix_commands() == ["meshtastic"]
    assert plugin.get_unmapped_room_ids() == []
    assert plugin._admin_room_id() is None
    assert plugin._min_power_level() == 50
    assert plugin._allow_destructive() is False
    assert plugin._timeout_seconds() == 120.0
    assert plugin._max_output_chars() == 2000
    assert plugin.get_require_bot_mention() is False


def test_admin_room_declared_as_unmapped_room() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    assert plugin._admin_room_id() == ADMIN_ROOM
    assert plugin.get_unmapped_room_ids() == [ADMIN_ROOM]


def test_config_overrides() -> None:
    plugin = _plugin(
        admin_room=ADMIN_ROOM,
        power_level="99",
        allow_destructive=True,
        timeout="60",
        max_output_chars="500",
        require_bot_mention=True,
    )
    assert plugin._min_power_level() == 99
    assert plugin._allow_destructive() is True
    assert plugin._timeout_seconds() == 60.0
    assert plugin._max_output_chars() == 500
    assert plugin.get_require_bot_mention() is True


def test_invalid_config_values_fall_back_to_defaults() -> None:
    plugin = _plugin(power_level="high", timeout="soon", max_output_chars="lots")
    assert plugin._min_power_level() == 50
    assert plugin._timeout_seconds() == 120.0
    assert plugin._max_output_chars() == 2000


def test_alias_config_is_inert() -> None:
    plugin = _plugin(admin_room="#ops:matrix.org")
    assert plugin._admin_room_id() is None
    assert plugin.get_unmapped_room_ids() == []


@pytest.mark.parametrize("setting", ["false", "true", 1, [], {}, None])
def test_destructive_opt_in_requires_literal_true(setting: Any) -> None:
    assert _plugin(allow_destructive=setting)._allow_destructive() is False


@pytest.mark.parametrize(
    ("setting", "key", "expected"),
    [
        (float("nan"), "timeout", 120.0),
        (float("inf"), "timeout", 120.0),
        (-100, "timeout", 120.0),
        (-1, "power_level", 50),
        (0, "power_level", 50),
        ("0", "power_level", 50),
        (True, "power_level", 50),
        (float("inf"), "power_level", 50),
        (-10, "max_output_chars", 2000),
    ],
)
def test_invalid_security_and_resource_settings_fall_back(
    setting: Any, key: str, expected: float | int
) -> None:
    plugin = _plugin(**{key: setting})
    accessor = {
        "timeout": plugin._timeout_seconds,
        "power_level": plugin._min_power_level,
        "max_output_chars": plugin._max_output_chars,
    }[key]
    assert accessor() == expected


# --- room and command gates --------------------------------------------------


async def test_ignores_other_rooms() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock()  # type: ignore[method-assign]
    result = await _call(
        plugin,
        _room(room_id="!elsewhere:matrix.org"),
        f"!meshtastic --dest {REMOTE_NODE_ID} --reboot",
    )
    assert result is False
    plugin._execute.assert_not_called()  # type: ignore[attr-defined]


async def test_ignores_non_command_messages() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    assert await _call(plugin, _room(), "hello there") is False


async def test_help_without_args() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    assert await _call(plugin, _room(), "!meshtastic")
    assert "Meshtastic CLI" in _last_message(plugin)


async def test_help_keyword() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    assert await _call(plugin, _room(), "!meshtastic help")
    message = _last_message(plugin)
    assert "--dest" in message
    assert "power level" in message.lower()


# --- authorization -----------------------------------------------------------


async def test_unauthorized_sender_refused() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock()  # type: ignore[method-assign]
    room = _room(users={ADMIN_SENDER: 0})
    with patch("mmrelay.matrix_utils.matrix_client", None):
        assert await _call(
            plugin, room, f"!meshtastic --dest {REMOTE_NODE_ID} --reboot"
        )
    plugin._execute.assert_not_called()  # type: ignore[attr-defined]
    assert "⛔" in _reactions(plugin)
    assert "Power level 50" in _last_message(plugin)


async def test_refusal_log_names_required_power_level() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock()  # type: ignore[method-assign]
    room = _room(users={ADMIN_SENDER: 0})
    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch.object(plugin, "logger") as mock_logger,
    ):
        assert await _call(
            plugin, room, f"!meshtastic --dest {REMOTE_NODE_ID} --reboot"
        )
    message = mock_logger.warning.call_args.args[0]
    assert "authorization requires power level %s in %s" in message
    assert mock_logger.warning.call_args.args[2] == 50
    assert mock_logger.warning.call_args.args[3] == ADMIN_ROOM


async def test_room_creator_authorized_despite_empty_users_map() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=CliCommandResult(exit_code=0, output="ok")
    )
    client = MagicMock()
    client.room_get_state = AsyncMock(
        return_value=RoomGetStateResponse(
            events=[
                {
                    "type": "m.room.create",
                    "sender": ADMIN_SENDER,
                    "state_key": "",
                    "content": {"room_version": "12"},
                }
            ],
            room_id=ADMIN_ROOM,
        )
    )
    with patch("mmrelay.matrix_utils.matrix_client", client):
        assert await _call(
            plugin,
            _room(users_default=0),
            f"!meshtastic --dest {REMOTE_NODE_ID} --reboot",
        )
    plugin._execute.assert_called_once()  # type: ignore[attr-defined]


async def test_missing_power_levels_refused() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock()  # type: ignore[method-assign]
    room = _room()
    room.power_levels = None
    with patch("mmrelay.matrix_utils.matrix_client", None):
        assert await _call(
            plugin, room, f"!meshtastic --dest {REMOTE_NODE_ID} --reboot"
        )
    plugin._execute.assert_not_called()  # type: ignore[attr-defined]


async def test_sender_at_threshold_authorized() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=CliCommandResult(exit_code=0, output="ok")
    )
    room = _room(users={ADMIN_SENDER: 50})
    assert await _call(plugin, room, f"!meshtastic --dest {REMOTE_NODE_ID} --reboot")
    plugin._execute.assert_called_once()  # type: ignore[attr-defined]


async def test_zero_configured_threshold_does_not_authorize_ordinary_members(
    matrix_state_client,
) -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM, power_level=0)
    plugin._execute = MagicMock()
    matrix_state_client.room_get_state.return_value = RoomGetStateResponse(
        events=[
            {
                "type": "m.room.power_levels",
                "state_key": "",
                "content": {"users": {}, "users_default": 0},
            }
        ],
        room_id=ADMIN_ROOM,
    )

    assert await _call(
        plugin, _room(users_default=0), f"!meshtastic --dest {REMOTE_NODE_ID} --reboot"
    )
    plugin._execute.assert_not_called()
    assert "Power level 50" in _last_message(plugin)


async def test_cached_authorized_sender_refused_when_client_unavailable() -> None:
    """A cached grant cannot authorize when fresh state is unavailable."""
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=CliCommandResult(exit_code=0, output="ok")
    )
    with patch("mmrelay.matrix_utils.matrix_client", None):
        assert await _call(
            plugin,
            _room(users={ADMIN_SENDER: 100}),
            f"!meshtastic --dest {REMOTE_NODE_ID} --reboot",
        )
    plugin._execute.assert_not_called()  # type: ignore[attr-defined]
    assert "⛔" in _reactions(plugin)


async def test_removed_admin_override_refused_before_radio_execution(
    matrix_state_client,
) -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock()
    matrix_state_client.room_get_state.return_value = RoomGetStateResponse(
        events=[
            {"type": "m.room.power_levels", "state_key": "", "content": {"users": {}}}
        ],
        room_id=ADMIN_ROOM,
    )

    assert await _call(
        plugin,
        _room(users={ADMIN_SENDER: 100}),
        f"!meshtastic --dest {REMOTE_NODE_ID} --reboot",
    )
    plugin._execute.assert_not_called()
    assert "⛔" in _reactions(plugin)


# --- execution and replies ----------------------------------------------------


async def test_successful_command_replies_with_output() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=CliCommandResult(exit_code=0, output="lora.region: 4")
    )
    assert await _call(
        plugin,
        _room(users={ADMIN_SENDER: 100}),
        f"!meshtastic --dest {REMOTE_NODE_ID} --get lora.region",
    )
    message = _last_message(plugin)
    assert "exit code 0" in message
    assert "lora.region: 4" in message
    assert "⏳" in _reactions(plugin)
    assert "✅" in _reactions(plugin)


async def test_failed_command_replies_with_output() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=CliCommandResult(exit_code=1, output="ERROR: no admin")
    )
    assert await _call(
        plugin,
        _room(users={ADMIN_SENDER: 100}),
        f"!meshtastic --dest {REMOTE_NODE_ID} --reboot",
    )
    message = _last_message(plugin)
    assert "exit code 1" in message
    assert "ERROR: no admin" in message
    assert "❌" in _reactions(plugin)


async def test_secret_args_are_not_logged_and_remote_output_is_plain_text() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin.logger = MagicMock()
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=CliCommandResult(
            exit_code=0, output="<img src=x onerror=alert(1)>"
        )
    )
    command = (
        f"!meshtastic --dest {REMOTE_NODE_ID} --ch-set-url https://example/?psk=secret"
    )
    assert await _call(plugin, _room(users={ADMIN_SENDER: 100}), command)
    log_arguments = repr(plugin.logger.info.call_args_list)
    assert "secret" not in log_arguments
    assert "sha256=" in log_arguments
    assert plugin.send_matrix_message.call_args.kwargs["formatted"] is False
    assert "<img src=x" in _last_message(plugin)


async def test_empty_output_reply_is_header_only() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=CliCommandResult(exit_code=0, output="")
    )
    assert await _call(
        plugin,
        _room(users={ADMIN_SENDER: 100}),
        f"!meshtastic --dest {REMOTE_NODE_ID} --reboot",
    )
    assert _last_message(plugin) == "✅ exit code 0"


async def test_long_output_truncated() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM, max_output_chars=50)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=CliCommandResult(exit_code=0, output="x" * 500)
    )
    assert await _call(
        plugin,
        _room(users={ADMIN_SENDER: 100}),
        f"!meshtastic --dest {REMOTE_NODE_ID} --get lora.region",
    )
    message = _last_message(plugin)
    assert "(truncated)" in message
    assert len(message) < 200


async def test_executor_refusal_reported() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        side_effect=CliCommandError("--dest is required")
    )
    assert await _call(plugin, _room(users={ADMIN_SENDER: 100}), "!meshtastic --reboot")
    assert "Refused: --dest is required" in _last_message(plugin)


async def test_unexpected_executor_failure_reported() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("boom")
    )
    assert await _call(
        plugin,
        _room(users={ADMIN_SENDER: 100}),
        f"!meshtastic --dest {REMOTE_NODE_ID} --reboot",
    )
    assert "failed unexpectedly" in _last_message(plugin)


async def test_busy_lock_rejects_new_command() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock()  # type: ignore[method-assign]
    await plugin._command_lock.acquire()
    try:
        assert await _call(
            plugin,
            _room(users={ADMIN_SENDER: 100}),
            f"!meshtastic --dest {REMOTE_NODE_ID} --reboot",
        )
        plugin._execute.assert_not_called()  # type: ignore[attr-defined]
        assert "still running" in _last_message(plugin)
    finally:
        plugin._command_lock.release()


async def test_command_lock_acquired_before_first_matrix_await() -> None:
    """Second command gets busy while the first reaction is pending."""
    plugin = _plugin(admin_room=ADMIN_ROOM)
    reaction_started = asyncio.Event()
    allow_reaction = asyncio.Event()

    async def pending_reaction(*_args: Any) -> None:
        reaction_started.set()
        await allow_reaction.wait()

    plugin.send_matrix_reaction = AsyncMock(side_effect=pending_reaction)  # type: ignore[method-assign]
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=CliCommandResult(exit_code=0, output="done")
    )
    command = f"!meshtastic --dest {REMOTE_NODE_ID} --reboot"
    room = _room(users={ADMIN_SENDER: 100})
    first = asyncio.create_task(_call(plugin, room, command))
    try:
        await asyncio.wait_for(reaction_started.wait(), 2)
        assert plugin._command_lock.locked()
        assert await asyncio.wait_for(_call(plugin, room, command), 2)
        assert "still running" in _last_message(plugin)
        plugin._execute.assert_not_called()  # type: ignore[attr-defined]
    finally:
        allow_reaction.set()
        await first
    plugin._execute.assert_called_once()  # type: ignore[attr-defined]


@pytest.mark.no_global_mocks
async def test_cancelled_handler_keeps_lock_until_radio_worker_finishes() -> None:
    """Handler cancellation must not allow a second concurrent radio dispatch."""
    plugin = _plugin(admin_room=ADMIN_ROOM)
    entered = threading.Event()
    release = threading.Event()

    def blocking_execute(_args: str) -> CliCommandResult:
        entered.set()
        assert release.wait(3)
        return CliCommandResult(exit_code=0, output="done")

    plugin._execute = MagicMock(side_effect=blocking_execute)  # type: ignore[method-assign]
    room = _room(users={ADMIN_SENDER: 100})
    command = f"!meshtastic --dest {REMOTE_NODE_ID} --reboot"
    first = asyncio.create_task(_call(plugin, room, command))
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 3), 4)
        first.cancel()
        await asyncio.sleep(0)
        assert plugin._command_lock.locked()
        assert await asyncio.wait_for(_call(plugin, room, command), 2)
        assert "still running" in _last_message(plugin)
        assert plugin._execute.call_count == 1  # type: ignore[attr-defined]
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
    assert not plugin._command_lock.locked()


# --- executor wiring -----------------------------------------------------------


def test_execute_passes_interface_and_settings() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM, allow_destructive=True, timeout="45")
    interface = MagicMock()
    interface.myInfo.my_node_num = 12345
    interface.isConnected = threading.Event()
    interface.isConnected.set()
    with (
        patch("mmrelay.meshtastic_utils.meshtastic_client", interface),
        patch("mmrelay.meshtastic_utils.reconnecting", False),
        patch("mmrelay.meshtastic_utils.shutting_down", False),
        patch("mmrelay.meshtastic_utils.connect_meshtastic") as mock_connect,
        patch(
            "mmrelay.plugins.meshtastic_cli_plugin.run_cli_command",
            return_value=CliCommandResult(exit_code=0, output="done"),
        ) as mock_run,
    ):
        result = plugin._execute(f"--dest {REMOTE_NODE_ID} --reboot")
    assert result.exit_code == 0
    mock_connect.assert_not_called()
    mock_run.assert_called_once_with(
        interface,
        f"--dest {REMOTE_NODE_ID} --reboot",
        local_node_num=12345,
        max_timeout_seconds=45.0,
        allow_destructive=True,
    )


def test_execute_reports_missing_interface() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    with (
        patch("mmrelay.meshtastic_utils.meshtastic_client", None),
        patch("mmrelay.meshtastic_utils.reconnecting", False),
        patch("mmrelay.meshtastic_utils.shutting_down", False),
        patch("mmrelay.meshtastic_utils.connect_meshtastic") as mock_connect,
    ):
        with pytest.raises(CliCommandError, match="connection is not ready"):
            plugin._execute(f"--dest {REMOTE_NODE_ID} --reboot")
    mock_connect.assert_not_called()


@pytest.mark.parametrize("state", ["reconnecting", "shutting_down"])
def test_execute_does_not_connect_during_recovery(state: str) -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    interface = MagicMock()
    interface.isConnected = threading.Event()
    interface.isConnected.set()
    with (
        patch("mmrelay.meshtastic_utils.meshtastic_client", interface),
        patch("mmrelay.meshtastic_utils.reconnecting", state == "reconnecting"),
        patch("mmrelay.meshtastic_utils.shutting_down", state == "shutting_down"),
        patch("mmrelay.meshtastic_utils.connect_meshtastic") as mock_connect,
        patch("mmrelay.plugins.meshtastic_cli_plugin.run_cli_command") as mock_run,
    ):
        with pytest.raises(CliCommandError, match="connection is not ready"):
            plugin._execute("--get lora.region")
    mock_connect.assert_not_called()
    mock_run.assert_not_called()


@pytest.mark.parametrize("connected", [False, None])
def test_execute_requires_ready_shared_client(connected: bool | None) -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    interface = MagicMock()
    interface.isConnected = threading.Event() if connected is not None else None
    with (
        patch("mmrelay.meshtastic_utils.meshtastic_client", interface),
        patch("mmrelay.meshtastic_utils.reconnecting", False),
        patch("mmrelay.meshtastic_utils.shutting_down", False),
        patch("mmrelay.meshtastic_utils.connect_meshtastic") as mock_connect,
        patch("mmrelay.plugins.meshtastic_cli_plugin.run_cli_command") as mock_run,
    ):
        with pytest.raises(CliCommandError, match="connection is not ready"):
            plugin._execute("--get lora.region")
    mock_connect.assert_not_called()
    mock_run.assert_not_called()


# --- unmapped-room integration ---------------------------------------------------


async def test_admin_room_dispatch_claims_command() -> None:
    """The unmapped-room dispatch path reaches the plugin that owns the room."""
    plugin = _plugin(admin_room=ADMIN_ROOM)
    room = _room(users={ADMIN_SENDER: 100})
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=CliCommandResult(exit_code=0, output="rebooted")
    )
    with patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]):
        await _dispatch_unmapped_room_message(
            room, _event(f"!meshtastic --dest {REMOTE_NODE_ID} --reboot")
        )
    plugin._execute.assert_called_once()  # type: ignore[attr-defined]


# --- coverage of remaining branches -------------------------------------------


async def test_join_execution_reraises_worker_error_without_cancellation() -> None:
    async def fail() -> CliCommandResult:
        raise RuntimeError("radio exploded")

    worker = asyncio.create_task(fail())
    with contextlib.suppress(RuntimeError):
        await worker
    assert worker.done()
    with pytest.raises(RuntimeError, match="radio exploded"):
        await _join_execution(worker)


async def test_join_execution_raises_cancelled_after_draining_raised_worker() -> None:
    release = asyncio.Event()

    async def fail_soon() -> CliCommandResult:
        await release.wait()
        raise RuntimeError("radio exploded")

    worker = asyncio.create_task(fail_soon())
    join = asyncio.create_task(_join_execution(worker))
    await asyncio.sleep(0)
    join.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await join
    with contextlib.suppress(RuntimeError):
        await worker


def test_description_mentions_command_and_room_scope() -> None:
    plugin = _plugin()
    assert "!meshtastic" in plugin.description


def test_start_without_admin_room_logs_warning() -> None:
    plugin = _plugin()
    with (
        patch.object(plugin, "logger") as mock_logger,
        patch.object(BasePlugin, "start", lambda self: None),
    ):
        plugin.start()
    assert mock_logger.warning.called
    message = mock_logger.warning.call_args.args[0]
    assert "admin_room" in message


def test_start_names_alias_values_as_unusable() -> None:
    plugin = _plugin(admin_room="#admin:matrix.org")
    with (
        patch.object(plugin, "logger") as mock_logger,
        patch.object(BasePlugin, "start", lambda self: None),
    ):
        plugin.start()
    assert mock_logger.warning.called
    message = mock_logger.warning.call_args.args[0]
    assert "alias" in message


def test_domain_less_admin_room_is_accepted() -> None:
    room_id = "!Ah4tQ7vZn9WkP2mXrB6yJc8sLd3FgUeH5iK1oN0pRwz"
    plugin = _plugin(admin_room=room_id)
    assert plugin._admin_room_id() == room_id
    assert plugin.get_unmapped_room_ids() == [room_id]


def test_start_does_not_warn_for_domain_less_admin_room() -> None:
    plugin = _plugin(admin_room="!Ah4tQ7vZn9WkP2mXrB6yJc8sLd3FgUeH5iK1oN0pRwz")
    with (
        patch.object(plugin, "logger") as mock_logger,
        patch.object(BasePlugin, "start", lambda self: None),
        patch("mmrelay.matrix_utils.matrix_rooms", []),
    ):
        plugin.start()
    assert not mock_logger.warning.called


def test_malformed_room_id_localparts_are_rejected() -> None:
    assert _plugin(admin_room="!")._admin_room_id() is None
    assert _plugin(admin_room="!room:")._admin_room_id() is None
    assert _plugin(admin_room="!:server")._admin_room_id() is None


@pytest.mark.parametrize(
    "matrix_rooms_value",
    [
        [{"id": ADMIN_ROOM, "meshtastic_channel": 0}],
        {ADMIN_ROOM: {"meshtastic_channel": 0}},
        {"named-room": {"id": ADMIN_ROOM, "meshtastic_channel": 0}},
    ],
)
def test_start_warns_when_admin_room_is_mesh_mapped(
    matrix_rooms_value: Any,
) -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    with (
        patch.object(plugin, "logger") as mock_logger,
        patch.object(BasePlugin, "start", lambda self: None),
        patch(
            "mmrelay.plugins.base_plugin.config", {"matrix_rooms": matrix_rooms_value}
        ),
    ):
        plugin.start()
    assert mock_logger.warning.called
    message = mock_logger.warning.call_args.args[0]
    assert "matrix_rooms" in message


def test_start_uses_config_before_matrix_client_is_ready() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    with (
        patch.object(plugin, "logger") as mock_logger,
        patch.object(BasePlugin, "start", lambda self: None),
        patch(
            "mmrelay.plugins.base_plugin.config", {"matrix_rooms": [{"id": ADMIN_ROOM}]}
        ),
        patch("mmrelay.matrix_utils.matrix_rooms", []),
    ):
        plugin.start()
    mock_logger.warning.assert_called_once()


def test_start_does_not_warn_when_admin_room_is_not_mesh_mapped() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    with (
        patch.object(plugin, "logger") as mock_logger,
        patch.object(BasePlugin, "start", lambda self: None),
        patch(
            "mmrelay.plugins.base_plugin.config",
            {"matrix_rooms": [{"id": "!other:matrix.org"}]},
        ),
    ):
        plugin.start()
    assert not mock_logger.warning.called


def test_sender_power_level_returns_none_when_power_levels_raise() -> None:
    from mmrelay.matrix_utils import sender_power_level

    class RaisingLevels:
        @property
        def users(self) -> dict[str, int]:
            raise TypeError("unusable power levels")

        @property
        def defaults(self) -> SimpleNamespace:
            return SimpleNamespace(users_default=0)

    room = SimpleNamespace(power_levels=RaisingLevels())
    assert sender_power_level(room, ADMIN_SENDER) is None


def test_sender_power_level_returns_none_for_non_numeric_level() -> None:
    from mmrelay.matrix_utils import sender_power_level

    room = SimpleNamespace(
        power_levels=SimpleNamespace(
            users={ADMIN_SENDER: "high"},
            defaults=SimpleNamespace(users_default=0),
        )
    )
    assert sender_power_level(room, ADMIN_SENDER) is None


async def test_handle_meshtastic_message_ignores_packets() -> None:
    plugin = _plugin()
    assert (
        await plugin.handle_meshtastic_message({}, "formatted", "longname", "meshnet")
        is False
    )


@pytest.mark.parametrize("matrix_rooms_value", [None, "not-a-mapping", 42])
def test_start_treats_non_container_matrix_rooms_as_unmapped(
    matrix_rooms_value: Any,
) -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    with (
        patch.object(plugin, "logger") as mock_logger,
        patch.object(BasePlugin, "start", lambda self: None),
        patch(
            "mmrelay.plugins.base_plugin.config", {"matrix_rooms": matrix_rooms_value}
        ),
    ):
        plugin.start()
    assert not mock_logger.warning.called


async def test_missing_matrix_client_fails_closed_without_reconnect() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock()  # type: ignore[method-assign]
    with (
        patch("mmrelay.matrix_utils.matrix_client", None),
        patch(
            "mmrelay.matrix_utils.connect_matrix",
            new=AsyncMock(side_effect=AssertionError("unexpected reconnect")),
        ) as connect_again,
    ):
        assert await _call(
            plugin,
            _room(users={ADMIN_SENDER: 0}),
            f"!meshtastic --dest {REMOTE_NODE_ID} --reboot",
        )
    plugin._execute.assert_not_called()  # type: ignore[attr-defined]
    connect_again.assert_not_awaited()
    assert "⛔" in _reactions(plugin)


@pytest.mark.parametrize(
    "room_id", [f" {ADMIN_ROOM}", f"{ADMIN_ROOM} ", "!admin:server name", "!admin\n"]
)
def test_admin_room_identity_is_validated_without_normalization(room_id: str) -> None:
    plugin = _plugin(admin_room=room_id)
    assert plugin._admin_room_id() is None
    assert plugin.get_unmapped_room_ids() == []
