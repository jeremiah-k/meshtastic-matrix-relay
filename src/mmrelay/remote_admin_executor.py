"""In-process mtjk admin-command runner for remote mesh nodes.

Runs one mtjk CLI command against the relay's existing Meshtastic interface
by driving mtjk's own argument parser and connected-action dispatcher — the
command surface therefore comes from the pinned mtjk package itself, and new
mtjk verbs appear as *denied* until they are classified here.

Safety contract:
- Every invocation must target an explicit, known remote node via ``--dest``;
  broadcast, the local pseudo-addresses, and the relay's own node are refused.
- Flags are classified against allow tables (read-only, routine, destructive);
  anything unclassified is denied. Destructive verbs additionally require the
  plugin's ``allow_destructive`` setting.
- The shared relay interface is never closed: dispatch's one-shot interface
  close is disarmed via ``ActionOutcome.interface_close_attempted``.
- CLI output and exit status are captured through mtjk's injectable
  ``cli_print``/``cli_exit`` hook seams, never stdout.

This module relies on mtjk's pinned CLI internals
(``meshtastic.cli.context``/``invocation``/``dispatch`` and the hook builder
in ``meshtastic.__main__``); mtjk is an exact-pin dependency of the relay.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import inspect
import math
import shlex
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any, NoReturn, cast

from mmrelay.log_utils import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from meshtastic.cli.dispatch import DispatchHooks as _DispatchHooks
    from meshtastic.mesh_interface import MeshInterface

logger = get_logger(__name__)

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

# Friendlier refusals for flags people will plausibly try.
DENY_REASONS: dict[str, str] = {
    "key_verify": "local-node only; key verification acts on the relay radio",
    "key_verify_nonce": "key verification is local-node only",
    "key_verify_security_number": "key verification is local-node only",
    "key_verify_wait": "key verification is local-node only",
    "device_metadata": "not exposed: results bypass the captured CLI print hook",
    "gpio_rd": "not exposed: results bypass the captured CLI print hook",
    "request_position": "not exposed: results bypass the captured CLI print hook",
    "request_telemetry": "not exposed: results bypass the captured CLI print hook",
    "traceroute": "not exposed: uses a separate destination and uncaptured results",
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
    "get_canned_message": "not exposed: results bypass the captured CLI print hook",
    "get_ringtone": "not exposed: results bypass the captured CLI print hook",
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


class AdminCommandError(Exception):
    """A refusal with a user-facing message; the command never ran."""


@dataclasses.dataclass(frozen=True)
class AdminCommandResult:
    """Outcome of one dispatched mtjk command."""

    exit_code: int
    output: str


class _CliExitSignal(Exception):
    """Raised by the injected cli_exit seam in place of sys.exit."""

    def __init__(self, message: str, return_value: int) -> None:
        super().__init__(message)
        self.return_value = return_value


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


def _parse_user_args(
    user_argv: list[str],
) -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    """Parse user tokens with mtjk's own parser and return it with the namespace."""
    from meshtastic.cli.parser import parse_cli_args

    # Reject option abbreviations: the policy must see the exact submitted flag.
    parser = _SilentArgumentParser(add_help=False, allow_abbrev=False)
    try:
        args = parse_cli_args(parser, version=_mtjk_version(), argv=user_argv)
    except SystemExit:
        raise AdminCommandError(
            "unrecognized arguments; see !admin help for the allowed commands"
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
    if problems:
        raise AdminCommandError("; ".join(problems))
    if getattr(args, "dry_run", False):
        unsafe = [
            action
            for action in _used_actions(parser, args)
            if action.dest not in MODIFIER_DESTS | {"dest", "timeout", "set"}
        ]
        if unsafe:
            raise AdminCommandError("--dry-run is supported only with --set")
    if verb_count == 0:
        raise AdminCommandError(
            "no allowed command specified; see !admin help for the allowed commands"
        )


def _known_node_nums(interface: MeshInterface) -> set[int]:
    """Return the node numbers present in the relay's node database."""
    lock = getattr(interface, "_node_db_lock", None)
    with lock if lock is not None else contextlib.nullcontext():
        nodes = getattr(interface, "nodes", None) or {}
        return {
            node_num
            for node_num in (
                node.get("num") if isinstance(node, dict) else None
                for node in nodes.values()
            )
            if isinstance(node_num, int)
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
        raise AdminCommandError(
            "--dest <node> is required and must name a remote node "
            "(for example --dest !a4bf9d0c)"
        )
    dest = raw_dest.strip()
    if dest.lower() in (BROADCAST_ADDR, LOCAL_ADDR, "all", "local"):
        raise AdminCommandError(
            "commands must target a specific remote node, not the local or broadcast address"
        )
    try:
        node_num = toNodeNum(dest)
    except (TypeError, ValueError):
        raise AdminCommandError(
            f"unrecognized node id: {dest!r} (expected forms: !a4bf9d0c, 0xa4bf9d0c, or decimal)"
        ) from None
    if (
        isinstance(node_num, bool)
        or not isinstance(node_num, int)
        or not 0 < node_num <= BROADCAST_NODE_NUM
    ):
        raise AdminCommandError(f"unrecognized node id: {dest!r}")
    if node_num == BROADCAST_NODE_NUM:
        raise AdminCommandError("broadcast destinations are not allowed")
    interface_local = getattr(getattr(interface, "myInfo", None), "my_node_num", None)
    if isinstance(interface_local, int) and not isinstance(interface_local, bool):
        local_node_num = interface_local
    if (
        isinstance(local_node_num, bool)
        or not isinstance(local_node_num, int)
        or not 0 < local_node_num < BROADCAST_NODE_NUM
    ):
        raise AdminCommandError(
            "the relay's own node identity is unavailable; remote administration is refused"
        )
    if node_num == local_node_num:
        raise AdminCommandError(
            "refusing to run admin commands against the relay's own node"
        )
    known = _known_node_nums(interface)
    if not known:
        raise AdminCommandError(
            "the relay's node database is empty; no remote node can be verified"
        )
    if node_num not in known:
        raise AdminCommandError(
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


def _capturing_hooks(
    cli_print: Callable[[str], None],
    cli_exit: Callable[..., NoReturn],
) -> "_DispatchHooks":
    """Build mtjk dispatch hooks whose output and exits stay in-process."""
    import meshtastic.__main__ as cli_main

    hooks = cli_main._build_connected_dispatch_hooks()  # noqa: SLF001 - pinned CLI seam
    # mtjk's sink-aware --get path has a distinct required-output sink so
    # standalone --quiet can suppress banners without losing preference values.
    # The current exact pin predates it; prepare for that field conditionally.
    service_output: dict[str, Any] = {
        "cli_print": cli_print,
        "cli_exit": cli_exit,
    }
    if hasattr(hooks.services, "preference_print"):
        service_output["preference_print"] = cli_print
    return dataclasses.replace(
        hooks,
        cli_print=cli_print,
        device=dataclasses.replace(
            hooks.device, cli_print=cli_print, cli_exit=cli_exit
        ),
        channel_contact=dataclasses.replace(
            hooks.channel_contact, cli_print=cli_print, cli_exit=cli_exit
        ),
        configure=dataclasses.replace(
            hooks.configure, cli_print=cli_print, cli_exit=cli_exit
        ),
        services=dataclasses.replace(hooks.services, **service_output),
    )


def _run_dispatch(
    interface: MeshInterface,
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> AdminCommandResult:
    """Dispatch the parsed command on the shared interface and capture output."""
    from meshtastic.cli import dispatch as cli_dispatch
    from meshtastic.cli.context import ActionOutcome, CliContext
    from meshtastic.cli.invocation import CliInvocation, activate_invocation
    from meshtastic.mesh_interface import MeshInterface as _MeshInterface

    lines: list[str] = []

    def cli_print(message: str, *, force: bool = False) -> None:
        del force
        lines.append(str(message))

    def cli_exit(message: str, return_value: int = 1) -> NoReturn:
        lines.append(str(message))
        raise _CliExitSignal(str(message), int(return_value))

    hooks = _capturing_hooks(cli_print, cli_exit)

    if args.get:
        parameters = inspect.signature(hooks.services.get_pref).parameters
        if "cli_print" not in parameters:
            raise AdminCommandError(
                "--get requires a sink-capable mtjk release; the installed dependency cannot capture preference values"
            )
    if args.get_ui_config or args.request_connection_status:
        from meshtastic.node import Node

        if (
            "response_deadline"
            not in inspect.signature(
                Node._request_admin_response  # noqa: SLF001 - pinned command deadline seam
            ).parameters
        ):
            raise AdminCommandError(
                "this query requires a deadline-capable mtjk release; the installed dependency cannot bound response waits"
            )
    from mmrelay.remote_admin_connection import _AdminConnection

    connection = _AdminConnection(interface, int(args.dest[1:], 16), args.timeout)
    outcome = ActionOutcome()
    # The relay owns the shared interface's lifetime; disarm dispatch's
    # one-shot close so admin commands can never drop the mesh connection.
    outcome.interface_close_attempted = True

    try:
        with activate_invocation(
            CliInvocation(args=args, parser=parser, channel_index=args.ch_index)
        ):
            context = CliContext(
                interface=cast("MeshInterface", connection),
                args=args,
                get_node_kwargs={
                    "requestChannelAttempts": args.channel_fetch_attempts,
                    "timeout": args.timeout,
                },
                outcome=outcome,
            )
            cli_dispatch._dispatch_connected(
                context, hooks
            )  # noqa: SLF001 - pinned CLI seam
            connection.waitForAckNak()
    except _CliExitSignal as signal:
        return AdminCommandResult(
            exit_code=signal.return_value, output="\n".join(lines).strip()
        )
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return AdminCommandResult(exit_code=code, output="\n".join(lines).strip())
    except _MeshInterface.MeshInterfaceError as exc:
        return AdminCommandResult(
            exit_code=1, output="\n".join([*lines, f"ERROR: {exc}"]).strip()
        )
    except TimeoutError as exc:
        return AdminCommandResult(
            exit_code=1,
            output="\n".join([*lines, f"ERROR: timed out: {exc}"]).strip(),
        )
    except Exception as exc:  # noqa: BLE001 - report any dispatch failure to the room
        logger.exception("Remote admin dispatch failed")
        return AdminCommandResult(
            exit_code=1,
            output="\n".join([*lines, f"ERROR: {type(exc).__name__}: {exc}"]).strip(),
        )
    finally:
        connection._cleanup()  # noqa: SLF001 - command owns the wait lifecycle
    return AdminCommandResult(exit_code=0, output="\n".join(lines).strip())


def run_admin_command(
    interface: MeshInterface,
    command_text: str,
    *,
    local_node_num: int | None = None,
    max_timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    allow_destructive: bool = False,
) -> AdminCommandResult:
    """Parse, vet, and run one mtjk admin command against a remote node.

    Raises AdminCommandError when the command is refused before dispatch;
    dispatch-time failures come back as an AdminCommandResult with a nonzero
    exit code and the captured output.
    """
    try:
        user_argv = shlex.split(command_text)
    except ValueError as exc:
        raise AdminCommandError(f"could not parse the command line: {exc}") from exc
    if not user_argv:
        raise AdminCommandError("no command given; see !admin help")

    parser, args = _parse_user_args(user_argv)
    _check_verb_policy(parser, args, allow_destructive=allow_destructive)
    _validate_destination(args, interface, local_node_num)
    if args.channel_fetch_attempts < 1:
        raise AdminCommandError("--channel-fetch-attempts must be at least 1")
    _clamp_timeout(args, max_timeout_seconds)
    return _run_dispatch(interface, parser, args)
