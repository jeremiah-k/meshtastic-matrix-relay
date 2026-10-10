"""In-process Meshtastic CLI runner for remote mesh nodes.

Runs an allowlisted mtjk embedded command against the relay-owned Meshtastic
interface. MMRelay validates its own room, verb, and remote-destination policy
before calling the public ``meshtastic.commands.executeCommand`` API.

Safety contract:
- Every invocation must target an explicit, known remote node via ``--dest``;
  broadcast, the local pseudo-addresses, and the relay's own node are refused.
- Flags are classified against allow tables (read-only, routine, destructive);
  anything unclassified is denied. Destructive verbs additionally require the
  plugin's ``allow_destructive`` setting, as do LoRa/security preference writes
  and channel identity/key changes through generic setters.
- The caller retains the radio connection: ``executeCommand`` never opens or
  closes it, uses request-scoped waits, and returns bounded captured output.
- The command surface is checked against the versioned embedded capabilities,
  and new mtjk actions remain denied until MMRelay explicitly permits them.
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import shlex
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from meshtastic.mesh_interface import MeshInterface

BROADCAST_NODE_NUM = 0xFFFFFFFF
BROADCAST_ADDR = "^all"
LOCAL_ADDR = "^local"

DEFAULT_COMMAND_TIMEOUT_SECONDS = 120.0

# Verbs that only read state from the remote node.
READ_ONLY_VERBS = frozenset(
    {
        "get",
        "get_ui_config",
        "request_connection_status",
        "get_canned_message",
        "get_ringtone",
    }
)

# Routine remote-admin verbs: state-changing but recoverable day-to-day
# administration of a remote node.
ROUTINE_VERBS = frozenset(
    {
        "ch_add",
        "ch_disable",
        "ch_enable",
        "ch_set",
        "reboot",
        "remove_favorite_node",
        "remove_node",
        "remove_position",
        "remove_ignored_node",
        "set",
        "pos_fields",
        "set_canned_message",
        "set_favorite_node",
        "set_ignored_node",
        "set_is_unmessageable",
        "set_owner",
        "set_owner_short",
        "set_ringtone",
        "set_time",
        "setalt",
        "setlat",
        "setlon",
        "toggle_muted_node",
        "backup_preferences",
    }
)

# Flags that only tune how another verb runs; never sufficient on their own.
MODIFIER_DESTS = frozenset(
    {
        "ack",
        "channel_fetch_attempts",
        "ch_index",
        "dry_run",
    }
)

# Destructive verbs: wipe state, take the node off the mesh, drive hardware,
# or reshape mesh-wide channels. Require allow_destructive.
DESTRUCTIVE_VERBS = frozenset(
    {
        "ch_add_url",
        "ch_del",
        "ch_longfast",
        "ch_longmod",
        "ch_longslow",
        "ch_longturbo",
        "ch_medfast",
        "ch_medslow",
        "ch_preset",
        "ch_set_url",
        "ch_shortfast",
        "ch_shortslow",
        "ch_shortturbo",
        "ch_vlongslow",
        "delete_file",
        "enter_dfu",
        "factory_reset",
        "factory_reset_device",
        "gpio_wrb",
        "input_kb_char",
        "input_touch_x",
        "input_touch_y",
        "reboot_ota",
        "remove_backup_preferences",
        "reset_nodedb",
        "restore_preferences",
        "send_input_event",
        "set_ham",
        "shutdown",
    }
)

DESTRUCTIVE_SET_SECTIONS = frozenset(
    {"lora", "security", "bluetooth", "network", "mqtt", "serial"}
)
DESTRUCTIVE_CH_SET_FIELDS = frozenset({"psk", "name", "role"})

# Config sections the pinned mtjk defines that routine-tier --set may write;
# connectivity, credential, and serial-console sections require allow_destructive.
ROUTINE_SET_SECTIONS = frozenset(
    {
        "device",
        "position",
        "power",
        "display",
        "version",
        "external_notification",
        "store_forward",
        "range_test",
        "telemetry",
        "canned_message",
        "audio",
        "remote_hardware",
        "neighbor_info",
        "ambient_lighting",
        "detection_sensor",
        "paxcounter",
        "statusmessage",
        "traffic_management",
        "tak",
        "mesh_beacon",
    }
)
# Top-level ChannelSettings fields routine-tier --ch-set may write; the
# remaining known fields (psk, name) require allow_destructive.
ROUTINE_CH_SET_FIELDS = frozenset(
    {
        "channel_num",
        "id",
        "uplink_enabled",
        "downlink_enabled",
        "module_settings",
        "use_aead",
    }
)

# Friendlier refusals for flags people will plausibly try.
DENY_REASONS: dict[str, str] = {
    "key_verify": "local-node only; key verification acts on the relay radio",
    "key_verify_nonce": "key verification is local-node only",
    "key_verify_security_number": "key verification is local-node only",
    "key_verify_wait": "key verification is local-node only",
    "device_metadata": "not exposed: results bypass the captured CLI print hook",
    "gpio_rd": "not exposed: results bypass the captured CLI print hook",
    "request_position": "not enabled for remote administration",
    "request_telemetry": "not enabled for remote administration",
    "traceroute": "not enabled for remote administration; use dedicated routing tools",
    "add_contact": "contact import is not exposed",
    "ble": "a connection flag; the relay's own connection is used",
    "ble_auto_reconnect": "a connection flag; the relay's own connection is used",
    "ble_scan": "a connection flag; the relay's own connection is used",
    "configure": "bulk profile writes are not exposed",
    "contact_ignore": "contact import is not exposed",
    "contact_qr": "generates local files",
    "contact_verified": "contact import is not exposed",
    "debug": "diagnostic flag",
    "debuglib": "diagnostic flag",
    "export_config": "local-node only",
    "export_format": "local-node only",
    "host": "a connection flag; the relay's own connection is used",
    "info": "local-node only; use --get",
    "json": "only valid with field-listing commands",
    "listen": "interactive or long-running",
    "lockdown_boots": "lockdown is local USB only",
    "lockdown_disable": "lockdown is local USB only",
    "lockdown_lock_now": "lockdown is local USB only",
    "lockdown_passphrase": "lockdown is local USB only",
    "lockdown_passphrase_file": "lockdown is local USB only",
    "lockdown_provision": "lockdown is local USB only",
    "lockdown_unlock": "lockdown is local USB only",
    "lockdown_valid_until": "lockdown is local USB only",
    "lockdown_wait": "lockdown is local USB only",
    "lockdown_yes": "lockdown is local USB only",
    "no_nodes": "a connection flag; the relay's own connection is used",
    "no_time": "a no-op flag",
    "noproto": "interactive or long-running",
    "nodes": "local-node only; use the !nodes command",
    "ota_update": "local TCP node only",
    "port": "a connection flag; the relay's own connection is used",
    "power_ppk2_meter": "power tooling",
    "power_ppk2_supply": "power tooling",
    "power_riden": "power tooling",
    "power_sim": "power tooling",
    "power_stress": "power tooling",
    "power_voltage": "power tooling",
    "power_wait": "power tooling",
    "private": "message sending belongs to the relay itself",
    "qr": "generates local files",
    "qr_all": "generates local files",
    "reply": "interactive or long-running",
    "sendtext": "message sending belongs to the relay itself",
    "seriallog": "diagnostic flag",
    "show_fields": "local-node only; use the !nodes command",
    "show_region_presets": "local-node only",
    "slog": "diagnostic flag",
    "store_ui_config": "local-node only",
    "support": "diagnostic flag",
    "test": "diagnostic flag",
    "tunnel": "local-node only",
    "tunnel_net": "local-node only",
    "wait_to_disconnect": "interactive or long-running",
}


class CliCommandError(Exception):
    """A refusal with a user-facing message; the command never ran."""


@dataclasses.dataclass(frozen=True)
class CliCommandResult:
    """Outcome of one dispatched mtjk command."""

    exit_code: int
    output: str


def _mtjk_version() -> str:
    """Return the pinned mtjk distribution version for the CLI parser."""
    try:
        return version("mtjk")
    except PackageNotFoundError:
        return "unknown"


class _SilentArgumentParser(argparse.ArgumentParser):
    """Keep rejected arguments and parser help out of the relay's process output."""

    def _print_message(self, message: str, file: object | None = None) -> None:
        """Suppress argparse diagnostics, help, and version output for room commands."""


def _refuse_option_shaped_set_values(user_argv: list[str]) -> None:
    """Reject long-option tokens in the value slot of ``--set`` before parsing.

    Python versions differ on whether argparse consumes a recognized ``--flag``
    as the second of ``--set FIELD VALUE`` or rejects the missing operand. In
    neither case may a submitted flag silently become a device preference.
    Check the original argv so the refusal is the same on every Python version.
    Negative numeric values (for example ``-1``) are not long options.
    """
    for index, token in enumerate(user_argv):
        if token == "--set" and index + 2 < len(user_argv):
            value = user_argv[index + 2]
            if value.startswith("--"):
                raise CliCommandError(
                    f"{value.split('=', 1)[0]} cannot be used as a --set value "
                    "(place flags before --set or after its two operands)"
                )


def _parse_user_args(
    user_argv: list[str],
) -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    """Parse user tokens with mtjk's own parser and return it with the namespace."""
    _refuse_option_shaped_set_values(user_argv)
    from meshtastic.cli.parser import parse_cli_args

    # Reject option abbreviations: the policy must see the exact submitted flag.
    parser = _SilentArgumentParser(add_help=False, allow_abbrev=False)
    try:
        args = parse_cli_args(parser, version=_mtjk_version(), argv=user_argv)
    except SystemExit:
        raise CliCommandError(
            "unrecognized arguments; see !meshtastic help for the allowed commands"
        ) from None
    parser._mmrelay_supplied_options = frozenset(  # type: ignore[attr-defined]
        token.split("=", 1)[0]
        for token in user_argv
        if token.startswith("-") and token != "--"
    )
    return parser, args


def _used_actions(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> list[argparse.Action]:
    """Return explicitly supplied or non-default parser actions.

    argparse normalizes defaults, but the policy also needs flags supplied
    explicitly with their default values (including ``--flag=value``).
    """
    used: list[argparse.Action] = []
    supplied_options: frozenset[str] = getattr(
        parser, "_mmrelay_supplied_options", frozenset()
    )
    for (
        action
    ) in parser._actions:  # noqa: SLF001 - the action registry is the command surface
        if action.dest in ("help", "version"):
            continue
        value = getattr(args, action.dest, action.default)
        if value == action.default and not any(
            option in supplied_options for option in action.option_strings
        ):
            continue
        used.append(action)
    return used


def _primary_flag(action: argparse.Action) -> str:
    """Return the canonical flag spelling for an action."""
    if action.option_strings:
        return action.option_strings[0]
    return f"--{action.dest}"


def _iter_value_strings(value: object) -> Iterator[str]:
    """Yield the scalar strings inside a parsed argparse value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_value_strings(item)


def _refuse_swallowed_options(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Refuse option tokens that argparse consumed as plain values.

    Some argparse versions absorb flag-looking values after a fixed-n argument
    rather than reporting a missing operand. This defense catches such tokens
    anywhere in parsed values; the pre-parse check separately handles the
    second ``--set`` operand consistently across Python versions.
    """
    option_actions = parser._option_string_actions  # noqa: SLF001 - argparse registry
    value_strings = [
        token
        for action in parser._actions  # noqa: SLF001 - the action registry is the command surface
        if action.dest not in ("help", "version")
        for token in _iter_value_strings(getattr(args, action.dest, None))
    ]
    swallowed = [
        token
        for token in sorted(getattr(parser, "_mmrelay_supplied_options", frozenset()))
        if (action := option_actions.get(token)) is not None
        and action.dest not in ("help", "version")
        and getattr(args, action.dest, action.default) == action.default
        and token in value_strings
    ]
    if swallowed:
        raise CliCommandError(
            "; ".join(
                f"{token} was parsed as a value, not a flag (place flags "
                "before the operands of arguments such as --set)"
                for token in swallowed
            )
        )


def _mtjk_component_spelling(component: str) -> str:
    """Normalize one dotted preference component the way the pinned mtjk does.

    mtjk resolves --set/--ch-set names through strip() + camel_to_snake() per
    dotted component, and camel_to_snake drops leading underscores, so
    ``_lora`` and ``__Security`` reach the destructive sections. Reusing the
    same public helper keeps this gate's spelling in lockstep.
    """
    from meshtastic.util import camel_to_snake

    return camel_to_snake(component.strip())


def _check_verb_policy(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    allow_destructive: bool,
) -> None:
    """Refuse every used flag that is not allowed at the configured tier.

    Fail closed: a flag absent from the allow tables is denied regardless of
    what future mtjk releases register.
    """
    _refuse_swallowed_options(parser, args)
    problems: list[str] = []
    verb_count = 0
    for action in _used_actions(parser, args):
        dest = action.dest
        if dest in ("dest", "timeout"):
            continue
        if dest in MODIFIER_DESTS:
            continue
        if dest in READ_ONLY_VERBS or dest in ROUTINE_VERBS:
            verb_count += 1
            continue
        if dest in DESTRUCTIVE_VERBS:
            if not allow_destructive:
                problems.append(
                    f"{_primary_flag(action)} requires allow_destructive: true"
                )
            else:
                verb_count += 1
            continue
        reason = DENY_REASONS.get(dest, "not allowed for remote admin")
        problems.append(f"{_primary_flag(action)} is {reason}")
    if not allow_destructive:
        # mtjk resolves --set strictly by its first component (a config
        # section) and --ch-set by its leading component (a ChannelSettings
        # field; a dotted suffix on a scalar field still writes the scalar).
        # Normalize each component exactly as mtjk does and fail closed on any
        # component the pinned mtjk does not define, so no spelling reaches a
        # destructive section while the field-level policy is in force.
        for field, _value in getattr(args, "set", None) or []:
            section = _mtjk_component_spelling(field.split(".", 1)[0])
            if section in DESTRUCTIVE_SET_SECTIONS:
                problems.append(f"--set {field} requires allow_destructive: true")
            elif section not in ROUTINE_SET_SECTIONS:
                problems.append(f"--set {field}: unrecognized config section")
        for field, _value in getattr(args, "ch_set", None) or []:
            name = _mtjk_component_spelling(field.split(".", 1)[0])
            if name in DESTRUCTIVE_CH_SET_FIELDS:
                problems.append(f"--ch-set {field} requires allow_destructive: true")
            elif name not in ROUTINE_CH_SET_FIELDS:
                problems.append(f"--ch-set {field}: unrecognized channel setting")
    if problems:
        raise CliCommandError("; ".join(problems))
    if getattr(args, "dry_run", False):
        unsafe = [
            action
            for action in _used_actions(parser, args)
            if action.dest not in MODIFIER_DESTS | {"dest", "timeout", "set"}
        ]
        if unsafe:
            raise CliCommandError("--dry-run is supported only with --set")
    if verb_count == 0:
        raise CliCommandError(
            "no allowed command specified; see !meshtastic help for the allowed commands"
        )


def _known_node_nums(interface: MeshInterface) -> set[int]:
    """Return the node numbers present in the relay's node database."""
    return {
        node_num
        for node in interface.queryNodes().nodes
        if isinstance((node_num := node.get("num")), int)
        and not isinstance(node_num, bool)
    }


def _validate_destination(
    args: argparse.Namespace,
    interface: MeshInterface,
    local_node_num: int | None,
) -> int:
    """Require an explicit --dest naming a known remote node; normalize it.

    Broadcast and the local pseudo-addresses are refused, as is the relay's
    own node: admin commands must never act on the node running the relay.
    """
    from meshtastic.util import toNodeNum

    raw_dest = getattr(args, "dest", None)
    if not isinstance(raw_dest, str) or not raw_dest.strip():
        raise CliCommandError(
            "--dest <node> is required and must name a remote node "
            "(for example --dest !a4bf9d0c)"
        )
    dest = raw_dest.strip()
    if dest.lower() in (BROADCAST_ADDR, LOCAL_ADDR, "all", "local"):
        raise CliCommandError(
            "commands must target a specific remote node, not the local or broadcast address"
        )
    try:
        node_num = toNodeNum(dest)
    except (TypeError, ValueError):
        raise CliCommandError(
            f"unrecognized node id: {dest!r} (expected forms: !a4bf9d0c, 0xa4bf9d0c, or decimal)"
        ) from None
    if (
        isinstance(node_num, bool)
        or not isinstance(node_num, int)
        or not 0 < node_num <= BROADCAST_NODE_NUM
    ):
        raise CliCommandError(f"unrecognized node id: {dest!r}")
    if node_num == BROADCAST_NODE_NUM:
        raise CliCommandError("broadcast destinations are not allowed")
    interface_local = getattr(getattr(interface, "myInfo", None), "my_node_num", None)
    if isinstance(interface_local, int) and not isinstance(interface_local, bool):
        local_node_num = interface_local
    if (
        isinstance(local_node_num, bool)
        or not isinstance(local_node_num, int)
        or not 0 < local_node_num < BROADCAST_NODE_NUM
    ):
        raise CliCommandError(
            "the relay's own node identity is unavailable; remote administration is refused"
        )
    if node_num == local_node_num:
        raise CliCommandError(
            "refusing to run admin commands against the relay's own node"
        )
    known = _known_node_nums(interface)
    if not known:
        raise CliCommandError(
            "the relay's node database is empty; no remote node can be verified"
        )
    if node_num not in known:
        raise CliCommandError(
            f"node {dest} is not in the relay's node database (see !nodes)"
        )
    args.dest = f"!{node_num:08x}"
    return node_num


def _clamp_timeout(args: argparse.Namespace, max_timeout_seconds: float) -> None:
    """Bound the command wait timeout by the plugin's configured maximum."""
    try:
        cap = float(max_timeout_seconds)
    except (TypeError, ValueError, OverflowError):
        cap = DEFAULT_COMMAND_TIMEOUT_SECONDS
    if not math.isfinite(cap) or cap < 1.0:
        cap = DEFAULT_COMMAND_TIMEOUT_SECONDS
    try:
        requested = float(args.timeout)
    except (AttributeError, TypeError, ValueError, OverflowError):
        requested = cap
    if not math.isfinite(requested):
        requested = cap
    args.timeout = max(1.0, min(requested, cap))


def _embedded_argv(
    parser: argparse.ArgumentParser, user_argv: list[str], normalized_dest: str
) -> list[str]:
    """Remove timeout and all destination aliases after full policy validation.

    The embedded API takes timeout as an invocation argument, not a CLI flag.
    Removing all recognized destination aliases prevents a second submitted
    flag from superseding the one whose target was verified against the node DB.
    """
    result = ["--dest", normalized_dest]
    index = 0
    option_actions = parser._option_string_actions  # noqa: SLF001 - argparse registry
    while index < len(user_argv):
        token = user_argv[index]
        name, separator, _value = token.partition("=")
        action = option_actions.get(name)
        if action is not None and action.dest in {"dest", "timeout"}:
            index += 1 if separator else 2
        else:
            result.append(token)
            index += 1
    return result


def _run_embedded(
    interface: MeshInterface,
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    user_argv: list[str],
) -> CliCommandResult:
    """Execute one policy-approved command using the public mtjk API."""
    from meshtastic.commands import executeCommand, getCommandCapabilities
    from meshtastic.errors import RequestRejectedError
    from meshtastic.protobuf import mesh_pb2

    capabilities = getCommandCapabilities()
    if capabilities.apiVersion != 1:
        raise CliCommandError("unsupported mtjk embedded command API version")
    argv = _embedded_argv(parser, user_argv, args.dest)
    available = set(capabilities.supportedOptions)
    parser_options = parser._option_string_actions  # noqa: SLF001 - parser registry
    unsupported = [
        token.partition("=")[0]
        for token in argv
        if token.partition("=")[0] in parser_options
        and token.partition("=")[0] not in available
    ]
    if unsupported:
        # Deliberately do not echo flag values (which can contain credentials).
        raise CliCommandError(
            "mtjk embedded commands do not support: "
            + ", ".join(sorted(set(unsupported)))
        )
    # Do not transform a command failure into success. The library returns the
    # original error and a bounded, UTF-8-safe captured output string.
    outcome = executeCommand(
        interface, argv, timeout=args.timeout, maxOutputBytes=64 * 1024
    )
    message = outcome.output.strip()
    if isinstance(outcome.error, RequestRejectedError):
        # Routing enum names are safe to report; arbitrary exception text may
        # contain credentials supplied with the command.
        reason: str | int | None = outcome.error.reason
        if isinstance(reason, int):
            enum_value = mesh_pb2.Routing.Error.DESCRIPTOR.values_by_number.get(reason)
            reason = enum_value.name if enum_value is not None else None
        failure = "ERROR: RequestRejectedError"
        if (
            isinstance(reason, str)
            and reason in mesh_pb2.Routing.Error.DESCRIPTOR.values_by_name
        ):
            failure += f": {reason}"
        message = f"{message}\n{failure}" if message else failure
    elif outcome.error is not None and not message:
        message = f"ERROR: {type(outcome.error).__name__}"
    if outcome.truncated:
        message += "\n… (mtjk output truncated)"
    return CliCommandResult(exit_code=outcome.exitCode, output=message)


def run_cli_command(
    interface: MeshInterface,
    command_text: str,
    *,
    local_node_num: int | None = None,
    max_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    allow_destructive: bool = False,
) -> CliCommandResult:
    """Parse, vet, and run one mtjk admin command against a remote node.

    Raises CliCommandError when the command is refused before dispatch;
    dispatch-time failures come back as an CliCommandResult with a nonzero
    exit code and the captured output.
    """
    try:
        user_argv = shlex.split(command_text)
    except ValueError as exc:
        raise CliCommandError(f"could not parse the command line: {exc}") from exc
    if not user_argv:
        raise CliCommandError("no command given; see !meshtastic help")

    parser, args = _parse_user_args(user_argv)
    _check_verb_policy(parser, args, allow_destructive=allow_destructive)
    _validate_destination(args, interface, local_node_num)
    if getattr(args, "channel_fetch_attempts", 1) < 1:
        raise CliCommandError("--channel-fetch-attempts must be at least 1")
    _clamp_timeout(args, max_timeout_seconds)
    return _run_embedded(interface, parser, args, user_argv)
