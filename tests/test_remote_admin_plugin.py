#!/usr/bin/env python3
"""
Tests for the remote_admin Matrix plugin.

Covers configuration accessors, the admin-room gate, power-level
authorization, help output, command execution wiring, busy-lock rejection,
and reply formatting for executor results and refusals.
"""

import asyncio
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.matrix_utils import _dispatch_unmapped_room_message
from mmrelay.plugins.remote_admin_plugin import Plugin
from mmrelay.remote_admin_executor import AdminCommandError, AdminCommandResult

ADMIN_ROOM = "!admin:matrix.org"
ADMIN_SENDER = "@alice:matrix.org"
REMOTE_NODE_ID = "!a4bf9d0c"


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
    assert plugin.get_matrix_commands() == ["admin"]
    assert plugin.get_unmapped_room_ids() == []
    assert plugin._admin_room_id() is None
    assert plugin._min_power_level() == 50
    assert plugin._allow_destructive() is False
    assert plugin._timeout_seconds() == 120.0
    assert plugin._max_output_chars() == 2000
    assert plugin.get_require_bot_mention() is False


def test_admin_room_declared_as_unmapped_room() -> None:
    plugin = _plugin(admin_room=f"  {ADMIN_ROOM} ")
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
        f"!admin --dest {REMOTE_NODE_ID} --reboot",
    )
    assert result is False
    plugin._execute.assert_not_called()  # type: ignore[attr-defined]


async def test_ignores_non_command_messages() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    assert await _call(plugin, _room(), "hello there") is False


async def test_help_without_args() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    assert await _call(plugin, _room(), "!admin")
    assert "Remote admin" in _last_message(plugin)


async def test_help_keyword() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    assert await _call(plugin, _room(), "!admin help")
    message = _last_message(plugin)
    assert "--dest" in message
    assert "power level" in message.lower()


# --- authorization -----------------------------------------------------------


async def test_unauthorized_sender_refused() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock()  # type: ignore[method-assign]
    room = _room(users={ADMIN_SENDER: 0})
    assert await _call(plugin, room, f"!admin --dest {REMOTE_NODE_ID} --reboot")
    plugin._execute.assert_not_called()  # type: ignore[attr-defined]
    assert "⛔" in _reactions(plugin)
    assert "Power level 50" in _last_message(plugin)


async def test_missing_power_levels_refused() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock()  # type: ignore[method-assign]
    room = _room()
    room.power_levels = None
    assert await _call(plugin, room, f"!admin --dest {REMOTE_NODE_ID} --reboot")
    plugin._execute.assert_not_called()  # type: ignore[attr-defined]


async def test_sender_at_threshold_authorized() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=AdminCommandResult(exit_code=0, output="ok")
    )
    room = _room(users={ADMIN_SENDER: 50})
    assert await _call(plugin, room, f"!admin --dest {REMOTE_NODE_ID} --reboot")
    plugin._execute.assert_called_once()  # type: ignore[attr-defined]


def test_power_level_default_applies() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    assert plugin._sender_power_level(_room(users_default=75), ADMIN_SENDER) == 75
    assert (
        plugin._sender_power_level(
            _room(users={ADMIN_SENDER: 10}, users_default=75), ADMIN_SENDER
        )
        == 10
    )


# --- execution and replies ----------------------------------------------------


async def test_successful_command_replies_with_output() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=AdminCommandResult(exit_code=0, output="lora.region: 4")
    )
    assert await _call(
        plugin,
        _room(users={ADMIN_SENDER: 100}),
        f"!admin --dest {REMOTE_NODE_ID} --get lora.region",
    )
    message = _last_message(plugin)
    assert "exit code 0" in message
    assert "lora.region: 4" in message
    assert "⏳" in _reactions(plugin)
    assert "✅" in _reactions(plugin)


async def test_failed_command_replies_with_output() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=AdminCommandResult(exit_code=1, output="ERROR: no admin")
    )
    assert await _call(
        plugin,
        _room(users={ADMIN_SENDER: 100}),
        f"!admin --dest {REMOTE_NODE_ID} --reboot",
    )
    message = _last_message(plugin)
    assert "exit code 1" in message
    assert "ERROR: no admin" in message
    assert "❌" in _reactions(plugin)


async def test_secret_args_are_not_logged_and_remote_output_is_plain_text() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin.logger = MagicMock()
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=AdminCommandResult(
            exit_code=0, output="<img src=x onerror=alert(1)>"
        )
    )
    command = f"!admin --dest {REMOTE_NODE_ID} --ch-set-url https://example/?psk=secret"
    assert await _call(plugin, _room(users={ADMIN_SENDER: 100}), command)
    log_arguments = repr(plugin.logger.info.call_args_list)
    assert "secret" not in log_arguments
    assert "sha256=" in log_arguments
    assert plugin.send_matrix_message.call_args.kwargs["formatted"] is False
    assert "<img src=x" in _last_message(plugin)


async def test_empty_output_reply_is_header_only() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=AdminCommandResult(exit_code=0, output="")
    )
    assert await _call(
        plugin,
        _room(users={ADMIN_SENDER: 100}),
        f"!admin --dest {REMOTE_NODE_ID} --reboot",
    )
    assert _last_message(plugin) == "✅ exit code 0"


async def test_long_output_truncated() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM, max_output_chars=50)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=AdminCommandResult(exit_code=0, output="x" * 500)
    )
    assert await _call(
        plugin,
        _room(users={ADMIN_SENDER: 100}),
        f"!admin --dest {REMOTE_NODE_ID} --get lora.region",
    )
    message = _last_message(plugin)
    assert "(truncated)" in message
    assert len(message) < 200


async def test_executor_refusal_reported() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        side_effect=AdminCommandError("--dest is required")
    )
    assert await _call(plugin, _room(users={ADMIN_SENDER: 100}), "!admin --reboot")
    assert "Refused: --dest is required" in _last_message(plugin)


async def test_unexpected_executor_failure_reported() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("boom")
    )
    assert await _call(
        plugin,
        _room(users={ADMIN_SENDER: 100}),
        f"!admin --dest {REMOTE_NODE_ID} --reboot",
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
            f"!admin --dest {REMOTE_NODE_ID} --reboot",
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
        return_value=AdminCommandResult(exit_code=0, output="done")
    )
    command = f"!admin --dest {REMOTE_NODE_ID} --reboot"
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

    def blocking_execute(_args: str) -> AdminCommandResult:
        entered.set()
        assert release.wait(3)
        return AdminCommandResult(exit_code=0, output="done")

    plugin._execute = MagicMock(side_effect=blocking_execute)  # type: ignore[method-assign]
    room = _room(users={ADMIN_SENDER: 100})
    command = f"!admin --dest {REMOTE_NODE_ID} --reboot"
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
    with (
        patch(
            "mmrelay.plugins.remote_admin_plugin.run_admin_command",
            return_value=AdminCommandResult(exit_code=0, output="done"),
        ) as mock_run,
        patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=interface),
    ):
        result = plugin._execute(f"--dest {REMOTE_NODE_ID} --reboot")
    assert result.exit_code == 0
    mock_run.assert_called_once_with(
        interface,
        f"--dest {REMOTE_NODE_ID} --reboot",
        local_node_num=12345,
        max_timeout_seconds=45.0,
        allow_destructive=True,
    )


def test_execute_reports_missing_interface() -> None:
    plugin = _plugin(admin_room=ADMIN_ROOM)
    with patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=None):
        with pytest.raises(AdminCommandError, match="Unable to connect"):
            plugin._execute(f"--dest {REMOTE_NODE_ID} --reboot")


# --- unmapped-room integration ---------------------------------------------------


async def test_admin_room_dispatch_claims_command() -> None:
    """The unmapped-room dispatch path reaches the plugin that owns the room."""
    plugin = _plugin(admin_room=ADMIN_ROOM)
    room = _room(users={ADMIN_SENDER: 100})
    plugin._execute = MagicMock(  # type: ignore[method-assign]
        return_value=AdminCommandResult(exit_code=0, output="rebooted")
    )
    with patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]):
        await _dispatch_unmapped_room_message(
            room, _event(f"!admin --dest {REMOTE_NODE_ID} --reboot")
        )
    plugin._execute.assert_called_once()  # type: ignore[attr-defined]
