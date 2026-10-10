#!/usr/bin/env python3
"""
Tests for the in-process Meshtastic CLI runner.

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

from mmrelay.meshtastic_cli_executor import (
    DESTRUCTIVE_CH_SET_FIELDS,
    DESTRUCTIVE_SET_SECTIONS,
    DESTRUCTIVE_VERBS,
    MODIFIER_DESTS,
    READ_ONLY_VERBS,
    ROUTINE_CH_SET_FIELDS,
    ROUTINE_SET_SECTIONS,
    ROUTINE_VERBS,
    CliCommandError,
    _check_verb_policy,
    _clamp_timeout,
    _mtjk_version,
    _parse_user_args,
    _refuse_swallowed_options,
    _validate_destination,
    run_cli_command,
)
from tests.meshtastic_cli_test_support import real_interface
from tests.meshtastic_cli_test_support import real_mtjk as real_mtjk

REMOTE_NODE_NUM = 0xA4BF9D0C
REMOTE_NODE_ID = "!a4bf9d0c"
LOCAL_NODE_NUM = REMOTE_NODE_NUM + 1


def _fake_interface() -> SimpleNamespace:
    interface = SimpleNamespace(
        myInfo=SimpleNamespace(my_node_num=LOCAL_NODE_NUM),
        nodes={
            REMOTE_NODE_ID: {"num": REMOTE_NODE_NUM},
            "!01020304": {"num": 0x01020304},
        },
    )
    interface.queryNodes = lambda: SimpleNamespace(
        nodes=tuple(interface.nodes.values())
    )
    return interface


def _parsed(command: str) -> tuple[Any, Any]:
    return _parse_user_args(shlex.split(command))


def _args_with(dest: str) -> tuple[Any, Any]:
    """Parse a valid base command, then mark an extra dest as used.

    Policy only consults dest names, so a truthy value that differs from
    every argparse default marks the flag used regardless of its CLI shape.
    """
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --reboot")
    value: Any = True
    if dest == "set":
        value = [["device.node_info_broadcast_secs", "900"]]
    elif dest == "ch_set":
        value = [["uplink_enabled", "true"]]
    setattr(args, dest, value)
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
    with pytest.raises(CliCommandError, match="unrecognized arguments"):
        _parsed("--dest !a4bf9d0c --definitely-not-a-flag")


def test_parse_rejects_help_flag() -> None:
    with pytest.raises(CliCommandError):
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
    with pytest.raises(CliCommandError, match="unrecognized arguments") as excinfo:
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
def test_remote_content_reads_use_embedded_capture(flag: str) -> None:
    """The new embedded API captures these formerly stdout-only reads."""
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} {flag}")
    _check_verb_policy(parser, args, allow_destructive=False)


@pytest.mark.parametrize("dest", sorted(DESTRUCTIVE_VERBS))
def test_destructive_verbs_require_opt_in(dest: str) -> None:
    parser, args = _args_with(dest)
    with pytest.raises(CliCommandError, match="allow_destructive"):
        _check_verb_policy(parser, args, allow_destructive=False)


@pytest.mark.parametrize("dest", sorted(DESTRUCTIVE_VERBS))
def test_destructive_verbs_allowed_with_opt_in(dest: str) -> None:
    parser, args = _args_with(dest)
    _check_verb_policy(parser, args, allow_destructive=True)


def test_set_verb_allowed() -> None:
    parser, args = _parsed(
        f"--dest {REMOTE_NODE_ID} --set device.node_info_broadcast_secs 900"
    )
    _check_verb_policy(parser, args, allow_destructive=False)


@pytest.mark.parametrize(
    "setter",
    [
        "--set lora.region US",
        "--set lora.modem_preset LONG_SLOW",
        "--set lora.modemPreset LONG_SLOW",
        "--set ' lora . region ' US",
        "--set security.admin_key public-key",
        "--set security.adminKey public-key",
        "--set ' security . adminKey ' public-key",
        "--set _lora.region US",
        "--set __Security.admin_key public-key",
        "--set bluetooth.fixed_pin 1234",
        "--set _Bluetooth.fixedPin 1234",
        "--set network.wifi_psk example-password",
        "--set ' network . wifiPsk ' example-password",
        "--set mqtt.password example-password",
        "--set serial.enabled true",
        "--ch-set psk none --ch-index 0",
        "--ch-set ' psk ' none --ch-index 0",
        "--ch-set _psk none --ch-index 0",
        "--ch-set psk.x 0x1a2b3c4d --ch-index 0",
        "--ch-set name changed --ch-index 0",
        "--ch-set _Name changed --ch-index 0",
        "--ch-set name.x changed --ch-index 0",
        "--ch-set role DISABLED --ch-index 0",
        "--set device.node_info_broadcast_secs 900 --set lora.region US",
    ],
)
@pytest.mark.parametrize("allow_destructive", [False, True])
def test_destructive_fields_require_opt_in_before_dispatch(
    setter: str, allow_destructive: bool
) -> None:
    from meshtastic.commands import CommandResult

    with patch(
        "meshtastic.commands.executeCommand", return_value=CommandResult(0, "ok")
    ) as execute:
        if allow_destructive:
            result = run_cli_command(
                _fake_interface(),
                f"--dest {REMOTE_NODE_ID} {setter}",
                allow_destructive=True,
            )
            assert result.exit_code == 0
            execute.assert_called_once()
        else:
            with pytest.raises(CliCommandError, match="requires allow_destructive"):
                run_cli_command(_fake_interface(), f"--dest {REMOTE_NODE_ID} {setter}")
            execute.assert_not_called()


def test_routine_channel_field_allowed_without_destructive_opt_in() -> None:
    parser, args = _parsed(
        f"--dest {REMOTE_NODE_ID} --ch-set uplink_enabled true --ch-index 1"
    )
    _check_verb_policy(parser, args, allow_destructive=False)


@pytest.mark.parametrize(
    "setter",
    [
        "--set _device.node_info_broadcast_secs 900",
        "--set Power.lsSecs 300",
        "--set device.nodeInfoBroadcastSecs 900",
        "--set mesh_beacon.flags 1",
        "--ch-set _uplink_enabled true --ch-index 1",
        "--ch-set uplinkEnabled true --ch-index 1",
        "--ch-set module_settings.position_precision 8 --ch-index 1",
    ],
)
def test_routine_set_spellings_follow_mtjk_normalization(setter: str) -> None:
    """Spellings mtjk normalizes onto routine fields stay routine-tier.

    The gate must accept exactly the spellings the pinned mtjk would apply,
    so parity with mtjk's per-component normalization never over-blocks.
    """
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} {setter}")
    _check_verb_policy(parser, args, allow_destructive=False)


@pytest.mark.parametrize(
    "setter,message",
    [
        ("--set region US", "unrecognized config section"),
        ("--set local_config.lora.region US", "unrecognized config section"),
        ("--set module.mesh_beacon.flags 1", "unrecognized config section"),
        ("--set telepathy.region US", "unrecognized config section"),
        ("--ch-set settings.psk none --ch-index 1", "unrecognized channel setting"),
        ("--ch-set voodoo changed --ch-index 1", "unrecognized channel setting"),
    ],
)
def test_unknown_set_components_fail_closed(setter: str, message: str) -> None:
    """Any component the pinned mtjk does not define is refused, not guessed.

    mtjk itself rejects these resolutions (bare leaf names, qualified
    prefixes, unknown sections), so refusing them in the gate fails closed
    against future mtjk resolution changes instead of relying on dispatch
    errors.
    """
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} {setter}")
    with pytest.raises(CliCommandError, match=message):
        _check_verb_policy(parser, args, allow_destructive=False)


def test_set_section_tables_cover_the_pinned_mtjk_schema() -> None:
    """Every section the pinned mtjk defines has exactly one tier assignment.

    A schema update that adds a section must fail here until the section is
    consciously classified as routine or destructive.
    """
    from meshtastic.protobuf import localonly_pb2, module_config_pb2

    sections = {
        field.name
        for config in (localonly_pb2.LocalConfig, module_config_pb2.ModuleConfig)
        for field in config.DESCRIPTOR.fields
    }
    assert not (ROUTINE_SET_SECTIONS & DESTRUCTIVE_SET_SECTIONS)
    assert ROUTINE_SET_SECTIONS | DESTRUCTIVE_SET_SECTIONS == sections


def test_ch_set_field_tables_cover_the_pinned_mtjk_schema() -> None:
    """Every ChannelSettings field the pinned mtjk defines is classified."""
    from meshtastic.protobuf import channel_pb2

    fields = {field.name for field in channel_pb2.ChannelSettings.DESCRIPTOR.fields}
    assert ROUTINE_CH_SET_FIELDS <= fields
    assert fields - ROUTINE_CH_SET_FIELDS <= set(DESTRUCTIVE_CH_SET_FIELDS)


def test_dry_run_modifier_needs_a_verb() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --dry-run")
    with pytest.raises(CliCommandError, match="no allowed command"):
        _check_verb_policy(parser, args, allow_destructive=False)


def test_dry_run_modifier_accompanies_verb() -> None:
    parser, args = _parsed(
        f"--dest {REMOTE_NODE_ID} --dry-run --set device.node_info_broadcast_secs 900"
    )
    _check_verb_policy(parser, args, allow_destructive=False)


def test_option_token_swallowed_by_set_values_is_refused() -> None:
    """Reject ``--set owner --dry-run`` on every supported Python version.

    argparse may reject the missing value or treat the flag as a literal
    value; either outcome must refuse execution before sending a radio write.
    """
    with pytest.raises(
        CliCommandError, match="--dry-run cannot be used as a --set value"
    ):
        _parsed(f"--dest {REMOTE_NODE_ID} --set owner --dry-run")


def test_swallowed_routine_verb_is_refused_not_silently_demoted() -> None:
    """A routine verb absorbed as a --set operand must not run as a string write.

    If argparse consumes ``--reboot`` as a value, both verbs otherwise pass
    the routine tier; the pre-parse guard prevents such a device write.
    """
    with pytest.raises(
        CliCommandError, match="--reboot cannot be used as a --set value"
    ):
        _parsed(f"--dest {REMOTE_NODE_ID} --set owner --reboot")


def test_option_shaped_set_value_refused_before_parser_version_dependent_handling() -> (
    None
):
    """Fail closed before argparse can consume or reject the second operand."""
    with patch("meshtastic.cli.parser.parse_cli_args") as parse:
        with pytest.raises(
            CliCommandError, match="--dry-run cannot be used as a --set value"
        ):
            _parsed(f"--dest {REMOTE_NODE_ID} --set owner --dry-run")
        parse.assert_not_called()


def test_flags_after_a_complete_set_pair_are_still_parsed() -> None:
    """Only operand slots absorb flags; after the pair, flags fire normally."""
    parser, args = _parsed(
        f"--dest {REMOTE_NODE_ID} --set device.node_info_broadcast_secs 900 --reboot"
    )
    _check_verb_policy(parser, args, allow_destructive=False)
    assert args.set == [["device.node_info_broadcast_secs", "900"]]


@pytest.mark.parametrize("dest", sorted(MODIFIER_DESTS))
def test_modifiers_alone_are_not_verbs(dest: str) -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID}")
    setattr(args, dest, True)
    args.reboot = False
    with pytest.raises(CliCommandError, match="no allowed command"):
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
    with pytest.raises(CliCommandError, match=reason):
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
    with pytest.raises(CliCommandError, match="local-node only"):
        _check_verb_policy(parser, args, allow_destructive=True)


def test_configure_flag_refused() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --configure profile.yaml")
    with pytest.raises(CliCommandError, match="not exposed"):
        _check_verb_policy(parser, args, allow_destructive=True)


def test_unclassified_future_verb_is_denied() -> None:
    """A verb mtjk adds later stays denied until classified here."""
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --reboot")
    args.some_future_verb = True
    action = next(a for a in parser._actions if a.dest == "reboot")  # noqa: SLF001
    action.dest = "some_future_verb"  # register the unknown dest on the parser
    try:
        with pytest.raises(CliCommandError, match="not allowed for remote admin"):
            _check_verb_policy(parser, args, allow_destructive=True)
    finally:
        action.dest = "reboot"


def test_multiple_problems_reported_together() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --nodes --info --shutdown")
    with pytest.raises(CliCommandError) as excinfo:
        _check_verb_policy(parser, args, allow_destructive=False)
    message = str(excinfo.value)
    assert "local-node only" in message
    assert "allow_destructive" in message


# --- destination rules ----------------------------------------------------


def test_destination_required() -> None:
    parser, args = _parsed("--get lora.region")
    with pytest.raises(CliCommandError, match="--dest"):
        _validate_destination(args, _fake_interface(), LOCAL_NODE_NUM)


@pytest.mark.parametrize(
    "bad", ["^all", "^local", "all", "local", "!ffffffff", "4294967295"]
)
def test_special_destinations_refused(bad: str) -> None:
    parser, args = _parsed(f"--dest {bad} --get lora.region")
    with pytest.raises(CliCommandError):
        _validate_destination(args, _fake_interface(), LOCAL_NODE_NUM)


def test_own_node_refused() -> None:
    parser, args = _parsed(f"--dest {LOCAL_NODE_NUM} --get lora.region")
    with pytest.raises(CliCommandError, match="relay's own node"):
        _validate_destination(args, _fake_interface(), LOCAL_NODE_NUM)


def test_unknown_node_refused() -> None:
    parser, args = _parsed("--dest !deadbeef --get lora.region")
    with pytest.raises(CliCommandError, match="node database"):
        _validate_destination(args, _fake_interface(), LOCAL_NODE_NUM)


def test_garbage_destination_refused() -> None:
    parser, args = _parsed("--dest nonsense --get lora.region")
    with pytest.raises(CliCommandError, match="unrecognized node id"):
        _validate_destination(args, _fake_interface(), LOCAL_NODE_NUM)


def test_empty_node_database_refused() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --get lora.region")
    with pytest.raises(CliCommandError, match="empty"):
        _validate_destination(
            args,
            SimpleNamespace(queryNodes=lambda: SimpleNamespace(nodes=())),
            LOCAL_NODE_NUM,
        )


def test_known_remote_destination_normalized() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_NUM} --get lora.region")
    node_num = _validate_destination(args, _fake_interface(), LOCAL_NODE_NUM)
    assert node_num == REMOTE_NODE_NUM
    assert args.dest == REMOTE_NODE_ID


def test_digit_only_bang_destination_is_hex_even_with_decimal_collision() -> None:
    interface = _fake_interface()
    interface.nodes["!12345678"] = {"num": 0x12345678}
    interface.nodes["!00bc614e"] = {"num": 12345678}
    _parser, args = _parsed("--dest !12345678 --get lora.region")

    assert _validate_destination(args, interface, LOCAL_NODE_NUM) == 0x12345678
    assert args.dest == "!12345678"


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


def test_run_admin_command_unbalanced_quotes_refused() -> None:
    with pytest.raises(CliCommandError, match="could not parse"):
        run_cli_command(_fake_interface(), '--set owner "unbalanced')


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
    with patch("mmrelay.meshtastic_cli_executor._run_embedded") as dispatch:
        with pytest.raises(CliCommandError):
            run_cli_command(_fake_interface(), f"--dest {REMOTE_NODE_ID} {command}")
    dispatch.assert_not_called()


@pytest.mark.parametrize("verb", ["--reboot", "--ch-add test", "--pos-fields ALTITUDE"])
def test_dry_run_cannot_accompany_a_mutating_nonpreview_verb(verb: str) -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --dry-run {verb}")
    with pytest.raises(CliCommandError, match="only with --set"):
        _check_verb_policy(parser, args, allow_destructive=True)


def test_destination_uses_interface_identity_even_if_caller_supplies_another() -> None:
    parser, args = _parsed(f"--dest {LOCAL_NODE_NUM} --reboot")
    with pytest.raises(CliCommandError, match="relay's own node"):
        _validate_destination(args, _fake_interface(), REMOTE_NODE_NUM)


def test_missing_local_identity_fails_closed() -> None:
    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --reboot")
    interface = _fake_interface()
    interface.myInfo = None
    with pytest.raises(CliCommandError, match="identity is unavailable"):
        _validate_destination(args, interface, None)


def test_zero_destination_is_refused_even_if_database_contains_it() -> None:
    parser, args = _parsed("--dest !00000000 --reboot")
    interface = _fake_interface()
    interface.nodes["!00000000"] = {"num": 0}
    with pytest.raises(CliCommandError, match="unrecognized node id"):
        _validate_destination(args, interface, LOCAL_NODE_NUM)


@pytest.mark.parametrize("attempts", [0, -1])
def test_channel_download_requires_a_positive_attempt_limit(attempts: int) -> None:
    with (
        patch("mmrelay.meshtastic_cli_executor._run_embedded") as dispatch,
        pytest.raises(CliCommandError, match="at least 1"),
    ):
        run_cli_command(
            _fake_interface(),
            f"--dest {REMOTE_NODE_ID} --reboot --channel-fetch-attempts {attempts}",
        )
    dispatch.assert_not_called()


def test_missing_channel_fetch_attempts_uses_default_limit() -> None:
    """Parser versions without the optional modifier remain usable."""
    from mmrelay.meshtastic_cli_executor import CliCommandResult

    radio = _fake_interface()
    original_parser = _parse_user_args

    def without_optional_argument(tokens: list[str]) -> tuple[Any, Any]:
        parser, args = original_parser(tokens)
        delattr(args, "channel_fetch_attempts")
        return parser, args

    with (
        patch(
            "mmrelay.meshtastic_cli_executor._parse_user_args",
            side_effect=without_optional_argument,
        ),
        patch(
            "mmrelay.meshtastic_cli_executor._run_embedded",
            return_value=CliCommandResult(0, "ok"),
        ) as dispatch,
    ):
        command = f"--dest {REMOTE_NODE_ID} --reboot"
        assert run_cli_command(radio, command).exit_code == 0
    dispatch.assert_called_once()


def test_primary_flag_names_positional_actions_by_dest() -> None:
    from mmrelay.meshtastic_cli_executor import _primary_flag

    action = SimpleNamespace(option_strings=[], dest="some_verb")
    assert _primary_flag(action) == "--some_verb"


def test_clamp_timeout_falls_back_to_default_on_unparseable_cap() -> None:
    args = SimpleNamespace(timeout=10)
    _clamp_timeout(args, "not-a-number")
    assert args.timeout == 10


def test_clamp_timeout_falls_back_to_cap_on_unparseable_request() -> None:
    args = SimpleNamespace(timeout=None)
    _clamp_timeout(args, 45)
    assert args.timeout == 45


def test_empty_command_is_refused() -> None:
    with pytest.raises(CliCommandError, match="no command given"):
        run_cli_command(_fake_interface(), "   ")


# --- current mtjk embedded API ---------------------------------------------


def test_embedded_dispatch_uses_public_api_without_closing_radio() -> None:
    from meshtastic.commands import CommandCapabilities, CommandResult

    from mmrelay.meshtastic_cli_executor import _run_embedded

    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --timeout=4 --get lora.region")
    radio = _fake_interface()
    _validate_destination(args, radio, LOCAL_NODE_NUM)
    _clamp_timeout(args, 30)
    with (
        patch(
            "meshtastic.commands.getCommandCapabilities",
            return_value=CommandCapabilities(1, ("--dest", "--get")),
        ),
        patch(
            "meshtastic.commands.executeCommand",
            return_value=CommandResult(0, "lora.region: 4\n"),
        ) as execute,
    ):
        result = _run_embedded(
            radio,
            parser,
            args,
            ["--dest", REMOTE_NODE_ID, "--timeout=4", "--get", "lora.region"],
        )
    assert result.exit_code == 0
    assert result.output == "lora.region: 4"
    execute.assert_called_once_with(
        radio,
        ["--dest", REMOTE_NODE_ID, "--get", "lora.region"],
        timeout=4,
        maxOutputBytes=64 * 1024,
    )


def test_embedded_refuses_options_not_in_versioned_capabilities() -> None:
    from meshtastic.commands import CommandCapabilities

    from mmrelay.meshtastic_cli_executor import _run_embedded

    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --dry-run --set lora.region 4")
    with (
        patch(
            "meshtastic.commands.getCommandCapabilities",
            return_value=CommandCapabilities(1, ("--dest", "--set")),
        ),
        patch("meshtastic.commands.executeCommand") as execute,
        pytest.raises(CliCommandError, match="--dry-run"),
    ):
        _run_embedded(
            _fake_interface(),
            parser,
            args,
            ["--dest", REMOTE_NODE_ID, "--dry-run", "--set", "lora.region", "4"],
        )
    execute.assert_not_called()


def test_embedded_failure_retains_exit_and_truncation() -> None:
    from meshtastic.commands import CommandCapabilities, CommandResult

    from mmrelay.meshtastic_cli_executor import _run_embedded

    parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --reboot")
    with (
        patch(
            "meshtastic.commands.getCommandCapabilities",
            return_value=CommandCapabilities(1, ("--dest", "--reboot")),
        ),
        patch(
            "meshtastic.commands.executeCommand",
            return_value=CommandResult(1, "ACK timed out\n", TimeoutError(), True),
        ),
    ):
        result = _run_embedded(
            _fake_interface(), parser, args, ["--dest", REMOTE_NODE_ID, "--reboot"]
        )
    assert result.exit_code == 1
    assert "ACK timed out" in result.output
    assert "truncated" in result.output


def test_run_admin_command_validates_and_forwards_to_embedded_api() -> None:
    from mmrelay.meshtastic_cli_executor import CliCommandResult

    radio = _fake_interface()
    with patch(
        "mmrelay.meshtastic_cli_executor._run_embedded",
        return_value=CliCommandResult(0, "sent"),
    ) as embedded:
        result = run_cli_command(
            radio,
            f"--dest {REMOTE_NODE_ID} --timeout 8 --reboot",
            max_timeout_seconds=12,
        )
    assert result.exit_code == 0
    args = embedded.call_args.args[2]
    assert args.dest == REMOTE_NODE_ID
    assert args.timeout == 8
    assert embedded.call_args.args[3] == [
        "--dest",
        REMOTE_NODE_ID,
        "--timeout",
        "8",
        "--reboot",
    ]


def test_embedded_argv_removes_duplicate_destination_aliases() -> None:
    from mmrelay.meshtastic_cli_executor import _embedded_argv

    parser, _ = _parsed(f"--dest !01020304 --reboot --dest={REMOTE_NODE_ID}")
    assert _embedded_argv(
        parser,
        ["--dest", "!01020304", "--reboot", f"--dest={REMOTE_NODE_ID}"],
        REMOTE_NODE_ID,
    ) == ["--dest", REMOTE_NODE_ID, "--reboot"]


@pytest.mark.parametrize("output", ["", "Partial output"])
@pytest.mark.parametrize("reason", ["PKI_FAILED", 34])
def test_embedded_routing_rejection_includes_reason(
    output: str, reason: str | int
) -> None:
    from meshtastic.errors import RequestRejectedError

    outcome = SimpleNamespace(
        exitCode=1,
        output=output,
        error=RequestRejectedError(
            reason,
            nodeNum=REMOTE_NODE_NUM,
            requestId=42,
            operation="get_config_request",
        ),
        truncated=False,
    )
    with (
        real_interface() as interface,
        patch("meshtastic.commands.executeCommand", return_value=outcome),
    ):
        result = run_cli_command(
            interface,
            f"--dest {REMOTE_NODE_ID} --get lora.region",
            local_node_num=LOCAL_NODE_NUM,
        )
    assert result.exit_code == 1
    assert result.output == (
        (output + "\n" if output else "") + "ERROR: RequestRejectedError: PKI_FAILED"
    )


@pytest.mark.parametrize("reason", ["secret-channel-key", 9999])
def test_embedded_unknown_routing_reason_does_not_disclose_exception_text(
    reason: str | int,
) -> None:
    from meshtastic.errors import RequestRejectedError

    outcome = SimpleNamespace(
        exitCode=1,
        output="Partial output",
        error=RequestRejectedError(reason, nodeNum=REMOTE_NODE_NUM),
        truncated=False,
    )
    with (
        real_interface() as interface,
        patch("meshtastic.commands.executeCommand", return_value=outcome),
    ):
        result = run_cli_command(
            interface,
            f"--dest {REMOTE_NODE_ID} --get lora.region",
            local_node_num=LOCAL_NODE_NUM,
        )
    assert result.output == "Partial output\nERROR: RequestRejectedError"


def test_unsupported_embedded_api_version_refuses_execution() -> None:
    """A future mtjk embedded API contract is refused before any dispatch."""
    with (
        patch(
            "meshtastic.commands.getCommandCapabilities",
            return_value=SimpleNamespace(apiVersion=2, supportedOptions=()),
        ),
        patch("meshtastic.commands.executeCommand") as dispatch,
    ):
        with pytest.raises(
            CliCommandError, match="unsupported mtjk embedded command API version"
        ):
            run_cli_command(
                _fake_interface(), f"--dest {REMOTE_NODE_ID} --get lora.hopLimit"
            )

    dispatch.assert_not_called()


def test_dispatch_error_without_output_reports_the_exception_type() -> None:
    """A failed dispatch with empty output still names the failure class."""
    outcome = SimpleNamespace(
        exitCode=1, output="", error=RuntimeError("boom"), truncated=False
    )
    with patch("meshtastic.commands.executeCommand", return_value=outcome):
        result = run_cli_command(
            _fake_interface(), f"--dest {REMOTE_NODE_ID} --get lora.hopLimit"
        )

    assert (result.exit_code, result.output) == (1, "ERROR: RuntimeError")


def test_mtjk_version_unknown_when_distribution_missing() -> None:
    from importlib.metadata import PackageNotFoundError

    with patch(
        "mmrelay.meshtastic_cli_executor.version",
        side_effect=PackageNotFoundError("mtjk"),
    ):
        assert _mtjk_version() == "unknown"


def test_supplied_option_swallowed_into_another_actions_value_is_refused() -> None:
    """The post-parse defense fires for any token at default inside a value.

    argparse version differences are irrelevant here: a supplied option that
    ended up at its default while its token appears inside another action's
    parsed value can never be the command the sender meant.
    """
    set_action = SimpleNamespace(dest="set", default=None)
    parser = SimpleNamespace(
        _option_string_actions={
            "--dry-run": SimpleNamespace(dest="dry_run", default=False)
        },
        _actions=[set_action],
        _mmrelay_supplied_options={"--dry-run"},
    )
    args = SimpleNamespace(set=["owner", "--dry-run"], dry_run=False)
    with pytest.raises(CliCommandError, match="parsed as a value"):
        _refuse_swallowed_options(parser, args)


@pytest.mark.parametrize("command", ["", "--unknown", "--dest !a4bf9d0c"])
def test_refusal_guidance_uses_registered_trigger(command: str) -> None:
    with pytest.raises(CliCommandError, match="!meshtastic help"):
        run_cli_command(_fake_interface(), command)


def test_destination_reads_detached_public_node_snapshot() -> None:
    from unittest.mock import Mock

    _parser, args = _parsed(f"--dest {REMOTE_NODE_ID} --get lora.region")
    query = Mock(return_value=SimpleNamespace(nodes=({"num": REMOTE_NODE_NUM},)))
    interface = SimpleNamespace(
        myInfo=SimpleNamespace(my_node_num=LOCAL_NODE_NUM), queryNodes=query
    )
    assert _validate_destination(args, interface, LOCAL_NODE_NUM) == REMOTE_NODE_NUM
    query.assert_called_once_with()
