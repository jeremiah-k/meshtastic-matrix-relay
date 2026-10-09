"""Matrix-facing remote admin plugin.

Exposes the ``!admin`` command in a single designated admin room. Users at or
above the configured power level can run mtjk admin verbs against remote
mesh nodes; the actual parsing, vetting, and in-process dispatch live in
:mmrelay.remote_admin_executor.
"""

import asyncio
import hashlib
import math
from typing import Any

from nio import (
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

from mmrelay.constants.config import CONFIG_KEY_REQUIRE_BOT_MENTION
from mmrelay.plugins.base_plugin import BasePlugin
from mmrelay.remote_admin_executor import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    AdminCommandError,
    AdminCommandResult,
    run_admin_command,
)

DEFAULT_ADMIN_POWER_LEVEL = 50
DEFAULT_MAX_OUTPUT_CHARS = 2000


async def _join_execution(task: asyncio.Task[AdminCommandResult]) -> AdminCommandResult:
    """Drain the worker under repeated cancellation before releasing the radio lock."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    try:
        result = task.result()
    except Exception:
        if cancelled:
            raise asyncio.CancelledError from None
        raise
    if cancelled:
        raise asyncio.CancelledError
    return result


class Plugin(BasePlugin):
    """Run mtjk admin commands against remote nodes from the admin room."""

    plugin_name = "remote_admin"
    is_core_plugin = True
    handles_unmapped_rooms = True

    def __init__(self, plugin_name: str | None = None) -> None:
        """Initialize the plugin and its single-command serialization lock."""
        super().__init__(plugin_name)
        self._command_lock = asyncio.Lock()

    @property
    def description(self) -> str:
        """Return the help-listing description."""
        return "Run mtjk admin commands against remote mesh nodes (!admin, admin room only)"

    def get_matrix_commands(self) -> list[str]:
        """Expose the ``admin`` Matrix command."""
        return ["admin"]

    def get_unmapped_room_ids(self) -> list[str]:
        """Declare the configured admin room as this plugin's own room."""
        admin_room = self._admin_room_id()
        return [admin_room] if admin_room else []

    def get_require_bot_mention(self) -> bool:
        """Do not require a bot mention in the dedicated admin room."""
        if CONFIG_KEY_REQUIRE_BOT_MENTION in self.config:
            return bool(self.config[CONFIG_KEY_REQUIRE_BOT_MENTION])
        return False

    def start(self) -> None:
        """Warn when the plugin is active without an admin room."""
        super().start()
        if not self._admin_room_id():
            self.logger.warning(
                "remote_admin requires admin_room to be a Matrix room ID "
                "(!room:server); aliases and missing values are not supported"
            )

    # --- configuration -----------------------------------------------------

    def _admin_room_id(self) -> str | None:
        value = self.config.get("admin_room")
        if isinstance(value, str):
            room_id = value.strip()
            if room_id.startswith("!") and ":" in room_id[1:]:
                return room_id
        return None

    def _min_power_level(self) -> int:
        value = self.config.get("power_level", DEFAULT_ADMIN_POWER_LEVEL)
        if isinstance(value, bool):
            return DEFAULT_ADMIN_POWER_LEVEL
        try:
            level = int(value)
        except (TypeError, ValueError, OverflowError):
            return DEFAULT_ADMIN_POWER_LEVEL
        return level if level >= 0 else DEFAULT_ADMIN_POWER_LEVEL

    def _allow_destructive(self) -> bool:
        return self.config.get("allow_destructive", False) is True

    def _timeout_seconds(self) -> float:
        try:
            value = float(self.config.get("timeout", DEFAULT_COMMAND_TIMEOUT_SECONDS))
        except (TypeError, ValueError, OverflowError):
            return DEFAULT_COMMAND_TIMEOUT_SECONDS
        return (
            value
            if math.isfinite(value) and value >= 1
            else DEFAULT_COMMAND_TIMEOUT_SECONDS
        )

    def _max_output_chars(self) -> int:
        try:
            value = int(self.config.get("max_output_chars", DEFAULT_MAX_OUTPUT_CHARS))
        except (TypeError, ValueError, OverflowError):
            return DEFAULT_MAX_OUTPUT_CHARS
        return value if value > 0 else DEFAULT_MAX_OUTPUT_CHARS

    # --- authorization -----------------------------------------------------

    def _sender_power_level(self, room: MatrixRoom, sender: str) -> int | None:
        """Return the sender's power level in the room, or None if unknown."""
        power_levels = getattr(room, "power_levels", None)
        if power_levels is None:
            return None
        try:
            users = power_levels.users
            default_level = power_levels.defaults.users_default
        except (AttributeError, TypeError):
            return None
        try:
            return int(users.get(sender, default_level))
        except (TypeError, ValueError):
            return None

    def _is_authorized(self, room: MatrixRoom, sender: str) -> bool:
        """Whether the sender meets the configured power-level threshold."""
        level = self._sender_power_level(room, sender)
        return level is not None and level >= self._min_power_level()

    # --- help --------------------------------------------------------------

    def _help_text(self) -> str:
        """Return the ``!admin help`` reply."""
        destructive_state = (
            "enabled"
            if self._allow_destructive()
            else "disabled (set allow_destructive: true)"
        )
        return (
            "**Remote admin — run mtjk commands against remote nodes**\n\n"
            "`!admin --dest <node> <mtjk flags>`\n\n"
            "Examples:\n"
            "- `!admin --dest !a4bf9d0c --get lora.region`\n"
            "- `!admin --dest !a4bf9d0c --reboot`\n"
            "- `!admin --dest !a4bf9d0c --request-connection-status`\n\n"
            "Rules:\n"
            "- `--dest` is required and must name a known remote node; the "
            "relay's own node is refused.\n"
            "- Read-only queries and routine admin verbs are allowed.\n"
            "- Destructive verbs (factory reset, node DB wipe, GPIO writes, "
            f"channel URL changes, …) are {destructive_state}.\n"
            "- Connection, local-node-only, and interactive mtjk flags are refused.\n"
            "- One command runs at a time; waits are bounded by the "
            f"configured timeout ({self._timeout_seconds():g}s).\n\n"
            f"Requires power level ≥ {self._min_power_level()} in this room."
        )

    # --- handlers ----------------------------------------------------------

    async def handle_meshtastic_message(
        self,
        packet: dict[str, Any],
        formatted_message: str,
        longname: str,
        meshnet_name: str,
    ) -> bool:
        """Ignore Meshtastic traffic; this plugin is Matrix-driven only."""
        _ = packet, formatted_message, longname, meshnet_name
        return False

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        """Handle ``!admin`` commands issued in the configured admin room."""
        _ = full_message
        admin_room = self._admin_room_id()
        if not admin_room or room.room_id != admin_room:
            return False

        parsed = self.get_matching_matrix_command_with_args(event)
        if parsed is None:
            return False
        _command, args_text = parsed
        args_text = args_text.strip()

        if not args_text or args_text.casefold() in ("help", "--help"):
            await self.send_matrix_message(
                room.room_id,
                self._help_text(),
                formatted=True,
                reply_to_event_id=event.event_id,
            )
            return True

        sender = event.sender
        if not self._is_authorized(room, sender):
            self.logger.warning(
                "Refused remote admin command from %s: power level below %s",
                sender,
                self._min_power_level(),
            )
            await self.send_matrix_reaction(room.room_id, event.event_id, "⛔")
            await self.send_matrix_message(
                room.room_id,
                f"Power level {self._min_power_level()} in this room is required "
                "to run admin commands.",
                formatted=False,
                reply_to_event_id=event.event_id,
            )
            return True

        if self._command_lock.locked():
            await self.send_matrix_message(
                room.room_id,
                "Another admin command is still running; try again once it finishes.",
                formatted=False,
                reply_to_event_id=event.event_id,
            )
            return True

        # Arguments may contain channel URLs or other credentials. The digest
        # correlates room commands with outcomes without writing secrets to logs.
        fingerprint = hashlib.sha256(args_text.encode("utf-8")).hexdigest()[:16]
        self.logger.info("Remote admin command from %s: sha256=%s", sender, fingerprint)
        try:
            # Acquire before the first await: concurrent commands never queue.
            async with self._command_lock:
                await self.send_matrix_reaction(room.room_id, event.event_id, "⏳")
                # Cancellation of the Matrix handler must not release the lock
                # while an in-flight synchronous radio command is still running.
                execution = asyncio.create_task(
                    asyncio.to_thread(self._execute, args_text)
                )
                result = await _join_execution(execution)
        except AdminCommandError as exc:
            await self.send_matrix_reaction(room.room_id, event.event_id, "❌")
            await self.send_matrix_message(
                room.room_id,
                f"Refused: {exc}",
                formatted=False,
                reply_to_event_id=event.event_id,
            )
            return True
        except Exception:  # noqa: BLE001 - report unexpected failures to the room
            self.logger.exception("Remote admin command failed")
            await self.send_matrix_reaction(room.room_id, event.event_id, "❌")
            await self.send_matrix_message(
                room.room_id,
                "The command failed unexpectedly; see the relay logs.",
                formatted=False,
                reply_to_event_id=event.event_id,
            )
            return True

        await self._reply_result(room, event, result)
        return True

    def _execute(self, args_text: str) -> AdminCommandResult:
        """Connect to the mesh and run one admin command (blocking)."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        interface = connect_meshtastic()
        if interface is None:
            raise AdminCommandError("Unable to connect to the Meshtastic device.")
        my_info = getattr(interface, "myInfo", None)
        local_node_num = getattr(my_info, "my_node_num", None)
        result = run_admin_command(
            interface,
            args_text,
            local_node_num=local_node_num,
            max_timeout_seconds=self._timeout_seconds(),
            allow_destructive=self._allow_destructive(),
        )
        self.logger.info(
            "Remote admin command finished with exit code %s", result.exit_code
        )
        return result

    async def _reply_result(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        result: AdminCommandResult,
    ) -> None:
        """React to the command and reply with the captured output."""
        emoji = "✅" if result.exit_code == 0 else "❌"
        await self.send_matrix_reaction(room.room_id, event.event_id, emoji)

        output = result.output.strip()
        header = f"{emoji} exit code {result.exit_code}"
        if not output:
            await self.send_matrix_message(
                room.room_id,
                header,
                formatted=False,
                reply_to_event_id=event.event_id,
            )
            return
        limit = self._max_output_chars()
        if len(output) > limit:
            output = output[:limit] + "\n… (truncated)"
        # The remote radio controls this output. Do not pass it into the
        # plugin's Markdown-to-HTML renderer as unsanitized formatted content.
        body = f"{header}\n\n{output}"
        await self.send_matrix_message(
            room.room_id,
            body,
            formatted=False,
            reply_to_event_id=event.event_id,
        )
