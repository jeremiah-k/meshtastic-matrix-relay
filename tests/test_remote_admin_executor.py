#!/usr/bin/env python3
"""
Tests for the in-process mtjk admin-command runner.

Covers argument parsing through mtjk's own parser, the fail-closed verb
policy tiers, remote-node destination rules, timeout clamping, and the
dispatch seam contract (output capture, exit mapping, interface-close guard).
"""

import math
import shlex
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from mmrelay.remote_admin_executor import (
    DESTRUCTIVE_VERBS,
    MODIFIER_DESTS,
    READ_ONLY_VERBS,
    ROUTINE_VERBS,
    AdminCommandError,
    _check_verb_policy,
    _clamp_timeout,
    _parse_user_args,
    _validate_destination,
    run_admin_command,
)
from tests.remote_admin_test_support import real_interface
from tests.remote_admin_test_support import real_mtjk as real_mtjk

REMOTE_NODE_NUM = 0xA4BF9D0C
REMOTE_NODE_ID = "!a4bf9d0c"
LOCAL_NODE_NUM = REMOTE_NODE_NUM + 1


def _fake_interface() -> SimpleNamespace:
    return SimpleNamespace(
        myInfo=SimpleNamespace(my_node_num=LOCAL_NODE_NUM),
        nodes={
            REMOTE_NODE_ID: {"num": REMOTE_NODE_NUM},
            "!01020304": {"num": 0x01020304},
        },
    )


def _parsed(command: str) -> tuple[Any, Any]:
    return _parse_user_args(shlex.split(command))


def _args_with(dest: str) -> tuple[Any, Any]:
    """Parse a valid base command, then mark an extra dest as used.

    Policy only consults dest names, so a truthy value that differs from
    every argparse default marks the flag used regardless of its CLI shape.
    """
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --reboot")
    setattr(args, dest, True)
    return parser, args


# --- table structure -----------------------------------------------------


def test_verb_tiers_are_disjoint() -> None:
    """A dest classified in two tiers would make gating ambiguous."""
    assert not (READ_ONLY_VERBS & ROUTINE_VERBS)
    assert not (READ_ONLY_VERBS & DESTRUCTIVE_VERBS)
    assert not (ROUTINE_VERBS & DESTRUCTIVE_VERBS)
    assert not (MODIFIER_DESTS & (READ_ONLY_VERBS | ROUTINE_VERBS | DESTRUCTIVE_VERBS))


# --- parsing --------------------------------------------------------------


def test_parse_accepts_remote_admin_command() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --get lora.region")
    assert args.dest == REMOTE_NODE_ID
    assert args.get == [["lora.region"]]
    assert parser is not None


def test_parse_rejects_unknown_tokens() -> None:
    with pytest.raises(AdminCommandError, match="unrecognized arguments"):
        _parsed("--dest !a4bf9d0c --definitely-not-a-flag")


def test_parse_rejects_help_flag() -> None:
    with pytest.raises(AdminCommandError):
        _parsed("--help")


@pytest.mark.parametrize(
    "command",
    [
        "--dest !a4bf9d0c --unknown secret-channel-key",
        "--dest !a4bf9d0c --timeout secret-channel-key --reboot",
        "--dest !a4bf9d0c --set secret-channel-key",
        "--help",
        "-h",
        "--version",
    ],
)
def test_parse_refusals_keep_process_output_empty(
    command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Parser errors and terminating actions cannot leak room commands to logs."""
    with pytest.raises(AdminCommandError, match="unrecognized arguments") as excinfo:
        _parsed(command)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert "secret-channel-key" not in str(excinfo.value)


# --- verb policy ----------------------------------------------------------


@pytest.mark.parametrize("dest", sorted(READ_ONLY_VERBS))
def test_read_only_verbs_allowed_without_flags(dest: str) -> None:
    parser, args = _args_with(dest)
    _check_verb_policy(parser, args, allow_destructive=False)


@pytest.mark.parametrize("dest", sorted(ROUTINE_VERBS))
def test_routine_verbs_allowed(dest: str) -> None:
    parser, args = _args_with(dest)
    _check_verb_policy(parser, args, allow_destructive=False)


@pytest.mark.parametrize("flag", ["--get-canned-message", "--get-ringtone"])
def test_uncaptured_remote_content_reads_are_refused(flag: str) -> None:
    """Bare stdout-only mtjk reads must not return empty Matrix success replies."""
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} {flag}")
    with pytest.raises(AdminCommandError, match="captured CLI print hook"):
        _check_verb_policy(parser, args, allow_destructive=True)


@pytest.mark.parametrize("dest", sorted(DESTRUCTIVE_VERBS))
def test_destructive_verbs_require_opt_in(dest: str) -> None:
    parser, args = _args_with(dest)
    with pytest.raises(AdminCommandError, match="allow_destructive"):
        _check_verb_policy(parser, args, allow_destructive=False)


@pytest.mark.parametrize("dest", sorted(DESTRUCTIVE_VERBS))
def test_destructive_verbs_allowed_with_opt_in(dest: str) -> None:
    parser, args = _args_with(dest)
    _check_verb_policy(parser, args, allow_destructive=True)


def test_set_verb_allowed() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --set lora.region 4")
    _check_verb_policy(parser, args, allow_destructive=False)


def test_dry_run_modifier_needs_a_verb() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --dry-run")
    with pytest.raises(AdminCommandError, match="no allowed command"):
        _check_verb_policy(parser, args, allow_destructive=False)


def test_dry_run_modifier_accompanies_verb() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --dry-run --set lora.region 4")
    _check_verb_policy(parser, args, allow_destructive=False)


@pytest.mark.parametrize("dest", sorted(MODIFIER_DESTS))
def test_modifiers_alone_are_not_verbs(dest: str) -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID}")
    setattr(args, dest, True)
    args.reboot = False
    with pytest.raises(AdminCommandError, match="no allowed command"):
        _check_verb_policy(parser, args, allow_destructive=False)


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ("--host radio.local", "connection flag"),
        ("--sendtext hi", "relay itself"),
        ("--nodes", "local-node only"),
        ("--info", "local-node only"),
        ("--listen", "long-running"),
        ("--qr", "local files"),
    ],
)
def test_hard_denied_flags_refused(extra: str, reason: str) -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --reboot {extra}")
    with pytest.raises(AdminCommandError, match=reason):
        _check_verb_policy(parser, args, allow_destructive=True)


@pytest.mark.parametrize("equals_form", [False, True])
def test_explicit_denied_option_at_default_is_still_denied(equals_form: bool) -> None:
    """Policy must not lose denied CLI options set to their parser default."""
    base_parser, _ = _parsed(f"--dest {REMOTE_NODE_ID} --reboot")
    action = next(a for a in base_parser._actions if a.dest == "export_format")
    option = next(o for o in action.option_strings if o.startswith("--"))
    value = str(action.default)
    extra = f"{option}={value}" if equals_form else f"{option} {value}"
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --reboot {extra}")
    assert getattr(args, action.dest) == action.default
    with pytest.raises(AdminCommandError, match="local-node only"):
        _check_verb_policy(parser, args, allow_destructive=True)


def test_configure_flag_refused() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --configure profile.yaml")
    with pytest.raises(AdminCommandError, match="not exposed"):
        _check_verb_policy(parser, args, allow_destructive=True)


def test_unclassified_future_verb_is_denied() -> None:
    """A verb mtjk adds later stays denied until classified here."""
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --reboot")
    args.some_future_verb = True
    action = next(a for a in parser._actions if a.dest == "reboot")  # noqa: SLF001
    action.dest = "some_future_verb"  # register the unknown dest on the parser
    try:
        with pytest.raises(AdminCommandError, match="not allowed for remote admin"):
            _check_verb_policy(parser, args, allow_destructive=True)
    finally:
        action.dest = "reboot"


def test_multiple_problems_reported_together() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --nodes --info --shutdown")
    with pytest.raises(AdminCommandError) as excinfo:
        _check_verb_policy(parser, args, allow_destructive=False)
    message = str(excinfo.value)
    assert "local-node only" in message
    assert "allow_destructive" in message


# --- destination rules ----------------------------------------------------


def test_destination_required() -> None:
    parser, args = _parsed("--get lora.region")
    with pytest.raises(AdminCommandError, match="--dest"):
        _validate_destination(args, _fake_interface(), LOCAL_NODE_NUM)


@pytest.mark.parametrize(
    "bad", ["^all", "^local", "all", "local", "!ffffffff", "4294967295"]
)
def test_special_destinations_refused(bad: str) -> None:
    parser, args = _parsed(f"--dest {bad} --get lora.region")
    with pytest.raises(AdminCommandError):
        _validate_destination(args, _fake_interface(), LOCAL_NODE_NUM)


def test_own_node_refused() -> None:
    parser, args = _parsed(f"--dest {LOCAL_NODE_NUM} --get lora.region")
    with pytest.raises(AdminCommandError, match="relay's own node"):
        _validate_destination(args, _fake_interface(), LOCAL_NODE_NUM)


def test_unknown_node_refused() -> None:
    parser, args = _parsed("--dest !deadbeef --get lora.region")
    with pytest.raises(AdminCommandError, match="node database"):
        _validate_destination(args, _fake_interface(), LOCAL_NODE_NUM)


def test_garbage_destination_refused() -> None:
    parser, args = _parsed("--dest nonsense --get lora.region")
    with pytest.raises(AdminCommandError, match="unrecognized node id"):
        _validate_destination(args, _fake_interface(), LOCAL_NODE_NUM)


def test_empty_node_database_refused() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --get lora.region")
    with pytest.raises(AdminCommandError, match="empty"):
        _validate_destination(args, SimpleNamespace(nodes={}), LOCAL_NODE_NUM)


def test_known_remote_destination_normalized() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_NUM} --get lora.region")
    node_num = _validate_destination(args, _fake_interface(), LOCAL_NODE_NUM)
    assert node_num == REMOTE_NODE_NUM
    assert args.dest == REMOTE_NODE_ID


# --- timeout clamping -----------------------------------------------------


def test_timeout_capped_to_plugin_maximum() -> None:
    _parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --get lora.region --timeout 300")
    _clamp_timeout(args, 60)
    assert args.timeout == 60


def test_shorter_timeout_preserved() -> None:
    _parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --get lora.region --timeout 30")
    _clamp_timeout(args, 60)
    assert args.timeout == 30


def test_default_timeout_capped() -> None:
    _parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --get lora.region")
    _clamp_timeout(args, 90)
    assert args.timeout == 90


@pytest.mark.parametrize("requested", ["nan", "inf", "-inf"])
def test_nonfinite_requested_timeout_never_reaches_dispatch(requested: str) -> None:
    _parser, args = _parsed(
        f"--dest {REMOTE_NODE_ID} --get lora.region --timeout={requested}"
    )
    _clamp_timeout(args, 60)
    assert args.timeout == 60
    assert math.isfinite(args.timeout)


@pytest.mark.parametrize("cap", [float("nan"), float("inf"), float("-inf"), -1.0])
def test_invalid_timeout_cap_defaults_to_finite_limit(cap: float) -> None:
    _parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --get lora.region --timeout inf")
    _clamp_timeout(args, cap)
    assert args.timeout == 120
    assert math.isfinite(args.timeout)


# --- end to end ------------------------------------------------------------


def test_run_admin_command_refusals_raise_before_dispatch() -> None:
    with patch("mmrelay.remote_admin_executor._run_dispatch") as mock_dispatch:
        with pytest.raises(AdminCommandError, match="--dest"):
            run_admin_command(_fake_interface(), "--get lora.region")
        with pytest.raises(AdminCommandError, match="relay itself"):
            run_admin_command(
                _fake_interface(),
                f"--dest {REMOTE_NODE_ID} --sendtext hi",
            )
    mock_dispatch.assert_not_called()


def test_run_admin_command_unbalanced_quotes_refused() -> None:
    with pytest.raises(AdminCommandError, match="could not parse"):
        run_admin_command(_fake_interface(), '--set owner "unbalanced')


def test_run_admin_command_normalizes_and_dispatches() -> None:
    captured: dict[str, Any] = {}

    def fake_dispatch(interface: Any, parser: Any, args: Any) -> Any:
        captured["interface"] = interface
        captured["args"] = args
        return "sentinel"

    interface = _fake_interface()
    with patch(
        "mmrelay.remote_admin_executor._run_dispatch", side_effect=fake_dispatch
    ):
        result = run_admin_command(
            interface,
            f"--dest {REMOTE_NODE_NUM} --get lora.region --timeout 999",
            local_node_num=LOCAL_NODE_NUM,
            max_timeout_seconds=45,
            allow_destructive=False,
        )
    assert result == "sentinel"
    assert captured["interface"] is interface
    assert captured["args"].dest == REMOTE_NODE_ID
    assert captured["args"].timeout == 45


# --- dispatch seam ---------------------------------------------------------


def _run_fake_dispatch(fake: Any) -> Any:
    """Run _run_dispatch against a patched mtjk connected dispatcher."""
    from mmrelay.remote_admin_executor import _run_dispatch

    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --reboot")
    _clamp_timeout(args, 60)
    with patch("meshtastic.cli.dispatch._dispatch_connected", side_effect=fake):
        return _run_dispatch(_fake_interface(), parser, args)


def test_capturing_hooks_routes_required_preference_results_to_sink() -> None:
    """New mtjk preference sinks must never revert to process stdout."""
    from dataclasses import dataclass
    from unittest.mock import MagicMock

    from mmrelay.remote_admin_executor import _capturing_hooks

    @dataclass(frozen=True)
    class Service:
        cli_print: Any = print
        cli_exit: Any = None
        preference_print: Any = print

    @dataclass(frozen=True)
    class General:
        cli_print: Any = print
        cli_exit: Any = None

    @dataclass(frozen=True)
    class Dispatch:
        cli_print: Any = print
        device: Any = General()
        channel_contact: Any = General()
        configure: Any = General()
        services: Any = Service()

    from meshtastic import __main__ as cli_main

    sink = MagicMock()
    with patch.object(
        cli_main, "_build_connected_dispatch_hooks", return_value=Dispatch()
    ):
        hooks = _capturing_hooks(sink, lambda *_args: None)

    hooks.services.preference_print("preference: value")
    sink.assert_called_once_with("preference: value")


def test_dispatch_captures_output_and_succeeds() -> None:
    def fake(context: Any, hooks: Any) -> None:
        hooks.cli_print("Connected to radio")
        hooks.cli_print("lora.region: 4")

    result = _run_fake_dispatch(fake)
    assert result.exit_code == 0
    assert "lora.region: 4" in result.output


def test_dispatch_maps_cli_exit_code_and_message() -> None:
    def fake(context: Any, hooks: Any) -> None:
        hooks.cli_print("working")
        hooks.device.cli_exit("ERROR: remote refused admin", 3)

    result = _run_fake_dispatch(fake)
    assert result.exit_code == 3
    assert "ERROR: remote refused admin" in result.output


def test_dispatch_maps_system_exit() -> None:
    def fake(context: Any, hooks: Any) -> None:
        raise SystemExit(2)

    result = _run_fake_dispatch(fake)
    assert result.exit_code == 2


def test_dispatch_maps_mesh_interface_error() -> None:
    from meshtastic.mesh_interface import MeshInterface

    def fake(context: Any, hooks: Any) -> None:
        raise MeshInterface.MeshInterfaceError("node unreachable")

    result = _run_fake_dispatch(fake)
    assert result.exit_code == 1
    assert "ERROR: node unreachable" in result.output


def test_dispatch_maps_timeout() -> None:
    def fake(context: Any, hooks: Any) -> None:
        raise TimeoutError("adminSessionPassKey")

    result = _run_fake_dispatch(fake)
    assert result.exit_code == 1
    assert "timed out" in result.output


def test_dispatch_maps_unexpected_exception() -> None:
    def fake(context: Any, hooks: Any) -> None:
        raise RuntimeError("surprise")

    result = _run_fake_dispatch(fake)
    assert result.exit_code == 1
    assert "ERROR: RuntimeError: surprise" in result.output


def test_dispatch_preserves_shared_interface_and_passes_context() -> None:
    """Dispatch runs with the close guard armed and clamped node kwargs."""
    captured: dict[str, Any] = {}

    def fake(context: Any, hooks: Any) -> None:
        captured["outcome"] = context.outcome
        captured["get_node_kwargs"] = context.get_node_kwargs
        captured["dest"] = context.destination

    result = _run_fake_dispatch(fake)
    assert result.exit_code == 0
    assert captured["outcome"].interface_close_attempted is True
    assert captured["get_node_kwargs"]["timeout"] == 60
    assert captured["dest"] == REMOTE_NODE_ID


@pytest.mark.parametrize(
    "command",
    [
        "--key-verify initiate",
        "--traceroute ^local",
        "--device-metadata",
        "--gpio-rd 1",
        "--request-telemetry",
        "--request-position",
    ],
)
def test_commands_outside_remote_capture_boundary_are_refused(command: str) -> None:
    with patch("mmrelay.remote_admin_executor._run_dispatch") as dispatch:
        with pytest.raises(AdminCommandError):
            run_admin_command(_fake_interface(), f"--dest {REMOTE_NODE_ID} {command}")
    dispatch.assert_not_called()


@pytest.mark.parametrize("verb", ["--reboot", "--ch-add test", "--pos-fields ALTITUDE"])
def test_dry_run_cannot_accompany_a_mutating_nonpreview_verb(verb: str) -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --dry-run {verb}")
    with pytest.raises(AdminCommandError, match="only with --set"):
        _check_verb_policy(parser, args, allow_destructive=True)


def test_destination_uses_interface_identity_even_if_caller_supplies_another() -> None:
    parser, args = _parsed(f"--dest {LOCAL_NODE_NUM} --reboot")
    with pytest.raises(AdminCommandError, match="relay's own node"):
        _validate_destination(args, _fake_interface(), REMOTE_NODE_NUM)


def test_missing_local_identity_fails_closed() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --reboot")
    interface = _fake_interface()
    interface.myInfo = None
    with pytest.raises(AdminCommandError, match="identity is unavailable"):
        _validate_destination(args, interface, None)


def test_zero_destination_is_refused_even_if_database_contains_it() -> None:
    parser, args = _parsed("--dest !00000000 --reboot")
    interface = _fake_interface()
    interface.nodes["!00000000"] = {"num": 0}
    with pytest.raises(AdminCommandError, match="unrecognized node id"):
        _validate_destination(args, interface, LOCAL_NODE_NUM)


def test_admin_connection_scopes_and_bounds_ack_wait_without_changing_interface() -> (
    None
):
    from meshtastic.protobuf import mesh_pb2

    from mmrelay.remote_admin_connection import _AdminConnection

    interface = real_interface()
    timeout = interface._timeout.expireTimeout
    connection = _AdminConnection(interface, REMOTE_NODE_NUM, 3)
    with (
        patch.object(
            interface, "_send_data_with_wait", return_value=mesh_pb2.MeshPacket(id=7)
        ),
        patch.object(interface, "_has_active_wait_request", return_value=True),
        patch.object(interface, "_wait_for_request_ack", return_value=True) as wait,
        patch.object(interface, "_retire_wait_request") as retire,
        patch.object(interface, "_raise_wait_error_if_present"),
    ):
        connection.sendData(b"admin", REMOTE_NODE_NUM, wantAck=True)
        connection.waitForAckNak()
        connection.waitForAckNak()
    assert wait.call_count == 1
    assert wait.call_args.args == ("receivedNak", 7)
    assert 0 < wait.call_args.kwargs["timeout_seconds"] <= 3
    retire.assert_called_once_with("receivedNak", request_id=7)
    assert interface._timeout.expireTimeout == timeout


@pytest.mark.parametrize("failure", [TimeoutError("no ACK"), ValueError("NAK")])
def test_admin_connection_retires_its_requests_on_failure(failure: Exception) -> None:
    from meshtastic.protobuf import mesh_pb2

    from mmrelay.remote_admin_connection import _AdminConnection

    interface = real_interface()
    connection = _AdminConnection(interface, REMOTE_NODE_NUM, 3)
    with (
        patch.object(
            interface, "_send_data_with_wait", return_value=mesh_pb2.MeshPacket(id=7)
        ),
        patch.object(interface, "_has_active_wait_request", return_value=True),
        patch.object(interface, "_wait_for_request_ack", side_effect=failure),
        patch.object(interface, "_retire_wait_request") as retire,
    ):
        connection.sendData(b"admin", REMOTE_NODE_NUM, wantAck=True)
        with pytest.raises(type(failure)):
            connection.waitForAckNak()
        connection._cleanup()
    retire.assert_called_once_with("receivedNak", request_id=7)


def test_admin_connection_rejects_actual_local_send() -> None:
    from mmrelay.remote_admin_connection import _AdminConnection

    interface = real_interface()
    connection = _AdminConnection(interface, REMOTE_NODE_NUM, 3)
    with patch.object(interface, "_send_data_with_wait") as send:
        with pytest.raises(ValueError, match="another node"):
            connection.sendData(b"admin", LOCAL_NODE_NUM, wantAck=True)
    send.assert_not_called()


def test_admin_connection_binds_ephemeral_node_and_timeout() -> None:
    from mmrelay.remote_admin_connection import _AdminConnection

    interface = real_interface()
    connection = _AdminConnection(interface, REMOTE_NODE_NUM, 3)
    node = connection.getNode(REMOTE_NODE_ID, False, timeout=300)
    assert node.iface is connection
    assert 0 < node._timeout.expireTimeout <= 3
    assert interface.localNode.iface is interface
    assert interface.localNode._timeout.expireTimeout == 300


def test_real_reboot_dispatch_uses_command_ack_scope_and_preserves_connection() -> None:
    from meshtastic.protobuf import mesh_pb2

    interface = real_interface()
    with (
        patch.object(
            interface, "_send_data_with_wait", return_value=mesh_pb2.MeshPacket(id=9)
        ) as send,
        patch.object(interface, "_has_active_wait_request", return_value=True),
        patch.object(interface, "_wait_for_request_ack", return_value=True) as wait,
        patch.object(interface, "_retire_wait_request"),
        patch.object(interface, "_raise_wait_error_if_present"),
        patch.object(interface, "close") as close,
    ):
        result = run_admin_command(
            interface, f"--dest {REMOTE_NODE_ID} --reboot", max_timeout_seconds=3
        )
    assert result.exit_code == 0, result.output
    assert "Connected to radio" in result.output
    assert send.call_args.args[1] == REMOTE_NODE_NUM
    assert wait.call_args.args == ("receivedNak", 9)
    assert 0 < wait.call_args.kwargs["timeout_seconds"] <= 3
    close.assert_not_called()


def test_uncapturable_get_is_refused_before_dispatch(capsys) -> None:
    from dataclasses import replace

    from meshtastic import __main__ as cli_main

    from mmrelay.remote_admin_executor import _run_dispatch

    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --get lora.region")
    hooks = cli_main._build_connected_dispatch_hooks()
    hooks = replace(
        hooks, services=replace(hooks.services, get_pref=lambda _node, _name: True)
    )
    with (
        patch.object(cli_main, "_build_connected_dispatch_hooks", return_value=hooks),
        patch("meshtastic.cli.dispatch._dispatch_connected") as dispatch,
        pytest.raises(AdminCommandError, match="sink-capable"),
    ):
        _run_dispatch(_fake_interface(), parser, args)
    dispatch.assert_not_called()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("verb", ["--get-ui-config", "--request-connection-status"])
def test_typed_queries_require_a_deadline_capable_dependency(verb) -> None:
    from meshtastic.node import Node

    with (
        patch.object(Node, "_request_admin_response", lambda *_args, **_kwargs: None),
        patch("meshtastic.cli.dispatch._dispatch_connected") as dispatch,
        pytest.raises(AdminCommandError, match="deadline-capable"),
    ):
        run_admin_command(_fake_interface(), f"--dest {REMOTE_NODE_ID} {verb}")
    dispatch.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("verb", "response_field", "expected"),
    [
        ("--get-ui-config", "get_ui_config_response", "{}"),
        (
            "--request-connection-status",
            "get_device_connection_status_response",
            "serial: connected",
        ),
    ],
)
def test_typed_queries_capture_their_correlated_response(
    verb, response_field, expected, capsys
):
    import inspect

    from meshtastic.node import Node
    from meshtastic.protobuf import admin_pb2, mesh_pb2

    if (
        "response_deadline"
        not in inspect.signature(Node._request_admin_response).parameters
    ):
        pytest.skip("the released mtjk pin predates the command-deadline seam")
    interface = real_interface()

    def respond(_data, _destination, **kwargs):
        response = admin_pb2.AdminMessage()
        field = getattr(response, response_field)
        field.SetInParent()
        if response_field == "get_device_connection_status_response":
            field.serial.is_connected = True
        kwargs["onResponse"]({"decoded": {"requestId": 29, "admin": {"raw": response}}})
        return mesh_pb2.MeshPacket(id=29)

    with (
        patch.object(interface, "_send_data_with_wait", side_effect=respond),
        patch.object(interface, "_retire_wait_request"),
        patch.object(interface, "close") as close,
    ):
        result = run_admin_command(
            interface, f"--dest {REMOTE_NODE_ID} {verb}", max_timeout_seconds=3
        )
    assert result.exit_code == 0, result.output
    assert expected in result.output
    assert capsys.readouterr() == ("", "")
    close.assert_not_called()


@pytest.mark.parametrize("acknowledged", [True, False])
def test_real_gpio_write_preserves_port_and_waits_for_its_ack(acknowledged) -> None:
    from meshtastic.protobuf import mesh_pb2, portnums_pb2

    interface = real_interface()
    interface.localNode.channels[0].settings.name = "gpio"
    with (
        patch.object(
            interface, "_send_data_with_wait", return_value=mesh_pb2.MeshPacket(id=19)
        ) as send,
        patch.object(interface, "_has_active_wait_request", return_value=True),
        patch.object(
            interface, "_wait_for_request_ack", return_value=acknowledged
        ) as wait,
        patch.object(interface, "_retire_wait_request"),
        patch.object(interface, "_raise_wait_error_if_present"),
        patch.object(interface, "close") as close,
    ):
        result = run_admin_command(
            interface,
            f"--dest {REMOTE_NODE_ID} --gpio-wrb 1 1",
            max_timeout_seconds=3,
            allow_destructive=True,
        )
    assert result.exit_code == (0 if acknowledged else 1), result.output
    assert send.call_args.args[1] == REMOTE_NODE_ID
    assert send.call_args.kwargs["portNum"] == portnums_pb2.REMOTE_HARDWARE_APP
    assert send.call_args.kwargs["response_wait_attr"] == "receivedNak"
    assert wait.call_args.args == ("receivedNak", 19)
    assert 0 < wait.call_args.kwargs["timeout_seconds"] <= 3
    if not acknowledged:
        assert "timed out" in result.output
    close.assert_not_called()


def test_expired_admin_connection_never_sends() -> None:
    from mmrelay.remote_admin_connection import _AdminConnection

    interface = real_interface()
    with (
        patch("mmrelay.remote_admin_connection.time.monotonic", side_effect=[10, 14]),
        patch.object(interface, "_send_data_with_wait") as send,
    ):
        connection = _AdminConnection(interface, REMOTE_NODE_NUM, 3)
        with pytest.raises(TimeoutError, match="deadline"):
            connection.sendData(b"admin", REMOTE_NODE_NUM, wantAck=True)
    send.assert_not_called()


@pytest.mark.parametrize("attempts", [0, -1])
def test_channel_download_requires_a_positive_attempt_limit(attempts) -> None:
    with (
        patch("mmrelay.remote_admin_executor._run_dispatch") as dispatch,
        pytest.raises(AdminCommandError, match="at least 1"),
    ):
        run_admin_command(
            _fake_interface(),
            f"--dest {REMOTE_NODE_ID} --reboot --channel-fetch-attempts {attempts}",
        )
    dispatch.assert_not_called()


def test_cleanup_retires_response_handlers_and_refuses_late_sends() -> None:
    from meshtastic.protobuf import mesh_pb2

    from mmrelay.remote_admin_connection import _AdminConnection

    interface = real_interface()
    connection = _AdminConnection(interface, REMOTE_NODE_NUM, 3)
    with (
        patch.object(
            interface, "_send_data_with_wait", return_value=mesh_pb2.MeshPacket(id=23)
        ) as send,
        patch.object(interface, "_retire_wait_request") as retire,
    ):
        connection.sendData(b"read", REMOTE_NODE_NUM, wantResponse=True)
        connection._cleanup()
        with pytest.raises(RuntimeError, match="completed"):
            connection.sendData(b"next", REMOTE_NODE_NUM, wantResponse=True)
    retire.assert_called_once_with("receivedNak", request_id=23)
    send.assert_called_once()


@pytest.mark.integration
def test_get_config_wait_uses_the_budget_remaining_after_ack(capsys) -> None:
    import inspect

    from meshtastic import __main__ as cli_main
    from meshtastic.protobuf import mesh_pb2

    if "cli_print" not in inspect.signature(cli_main.getPref).parameters:
        pytest.skip("the released mtjk pin predates the preference sink")
    clock = SimpleNamespace(now=10.0)
    interface = real_interface()

    def sleep(seconds):
        clock.now += seconds

    def acknowledge(*_args, **_kwargs):
        clock.now += 0.7
        return True

    with (
        patch("mmrelay.remote_admin_connection.time.monotonic", lambda: clock.now),
        patch("mmrelay.remote_admin_connection.time.sleep", sleep),
        patch.object(
            interface, "_send_data_with_wait", return_value=mesh_pb2.MeshPacket(id=31)
        ),
        patch.object(interface, "_has_active_wait_request", return_value=True),
        patch.object(interface, "_wait_for_request_ack", side_effect=acknowledge),
        patch.object(interface, "_retire_wait_request"),
        patch.object(interface, "_raise_wait_error_if_present"),
    ):
        result = run_admin_command(
            interface,
            f"--dest {REMOTE_NODE_ID} --get lora.region",
            max_timeout_seconds=1,
        )
    assert result.exit_code == 1
    assert clock.now == pytest.approx(11.0)
    assert "lora" in result.output
    assert capsys.readouterr() == ("", "")


@pytest.mark.integration
def test_real_get_dispatch_captures_a_fresh_remote_response(capsys) -> None:
    import inspect

    from meshtastic import __main__ as cli_main
    from meshtastic.protobuf import admin_pb2, mesh_pb2, portnums_pb2

    if "cli_print" not in inspect.signature(cli_main.getPref).parameters:
        pytest.skip("the released mtjk pin predates the preference sink")
    interface = real_interface()

    def respond(data: Any, destination: Any, **kwargs: Any) -> Any:
        response = admin_pb2.AdminMessage()
        response.get_config_response.lora.region = 1
        callback = kwargs.get("onResponse")
        if callback is not None:
            callback(
                {
                    "from": REMOTE_NODE_NUM,
                    "decoded": {
                        "portnum": portnums_pb2.PortNum.ADMIN_APP,
                        "payload": response.SerializeToString(),
                        "requestId": 17,
                        "admin": {
                            "getConfigResponse": {"lora": {"region": "US"}},
                            "raw": response,
                        },
                    },
                }
            )
        return mesh_pb2.MeshPacket(id=17)

    with (
        patch.object(interface, "_send_data_with_wait", side_effect=respond),
        patch.object(interface, "_has_active_wait_request", return_value=True),
        patch.object(interface, "_wait_for_request_ack", return_value=True),
        patch.object(interface, "_retire_wait_request"),
        patch.object(interface, "_raise_wait_error_if_present"),
        patch.object(interface, "close") as close,
    ):
        result = run_admin_command(
            interface,
            f"--dest {REMOTE_NODE_ID} --get lora.region",
            max_timeout_seconds=3,
        )
    assert result.exit_code == 0, result.output
    assert "lora.region: 1" in result.output
    assert capsys.readouterr() == ("", "")
    close.assert_not_called()
