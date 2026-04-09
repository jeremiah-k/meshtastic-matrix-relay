import asyncio
from typing import Any

# matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
from meshtastic.mesh_interface import BROADCAST_NUM
from nio import (
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

from mmrelay.constants.formats import DEFAULT_CHANNEL, TEXT_MESSAGE_APP
from mmrelay.constants.messages import (
    PING_FALLBACK_RESPONSE,
    PING_RESPONSE,
    PORTNUM_TEXT_MESSAGE_APP,
)
from mmrelay.constants.plugins import (
    MAX_PUNCTUATION_LENGTH,
    PING_COMMAND_REGEX,
    PING_EXPLICIT_COMMAND_REGEX,
)
from mmrelay.plugins.base_plugin import BasePlugin


def match_case(source: str, target: str) -> str:
    """
    Apply letter-case pattern of `source` to `target`.

    If `source` is empty an empty string is returned. If `target` is empty it is returned unchanged. If `target` is longer than `source`, `target` is truncated to `len(source)`. For mixed-case patterns, the effective length is the minimum of the two input lengths due to zip behavior. Common whole-string patterns are preserved: all-uppercase, all-lowercase, and title-case are applied to the entire `target`; mixed-case source patterns are applied character-by-character.

    Returns:
        str: The `target` string with its letters' case adjusted to match `source`.
    """
    if not source:
        return ""
    if not target:
        return target

    # If source and target have different lengths, truncate target to source length
    if len(source) != len(target):
        target = target[: len(source)]

    if source.isupper():
        return target.upper()
    elif source.islower():
        return target.lower()
    elif source.istitle():
        return target.capitalize()
    else:
        # For mixed case, match the pattern of each character
        return "".join(
            t.upper() if s.isupper() else t.lower()
            for s, t in zip(source, target, strict=False)
        )


class Plugin(BasePlugin):
    plugin_name = "ping"
    is_core_plugin = True
    _invalid_mimic_mode_warned: bool = False

    @property
    def description(self) -> str:
        return "Check connectivity with the relay; optional mimic mode responds to mesh pings"

    def get_mimic_mode(self) -> bool:
        mimic_mode = self.config.get("mimic_mode", False)
        if isinstance(mimic_mode, bool):
            return mimic_mode

        # Keep invalid config warnings low-noise while still surfacing operator errors.
        if not self._invalid_mimic_mode_warned:
            self.logger.warning(
                "Invalid ping.mimic_mode value %r; expected boolean. Defaulting to false.",
                mimic_mode,
            )
            self._invalid_mimic_mode_warned = True
        return False

    async def handle_meshtastic_message(
        self,
        packet: dict[str, Any],
        formatted_message: str,
        longname: str,
        meshnet_name: str,
    ) -> bool:
        _ = formatted_message, meshnet_name
        if "decoded" not in packet or "text" not in packet["decoded"]:
            return False

        portnum = packet["decoded"].get("portnum")
        if portnum is not None and str(portnum) not in {
            str(TEXT_MESSAGE_APP),
            str(PORTNUM_TEXT_MESSAGE_APP),
        }:
            return False

        message = packet["decoded"]["text"].strip()
        raw_channel = packet.get("channel")
        channel = DEFAULT_CHANNEL if raw_channel is None else raw_channel

        mimic_mode = self.get_mimic_mode()

        if mimic_mode:
            match = PING_COMMAND_REGEX.fullmatch(message)
            if not match:
                return False
            pre_punc = match.group(1)
            matched_text = match.group(2)
            post_punc = match.group(3)
        else:
            explicit_match = PING_EXPLICIT_COMMAND_REGEX.fullmatch(message)
            if not explicit_match:
                return False

        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = await asyncio.to_thread(connect_meshtastic)

        to_id = packet.get("to")
        if not meshtastic_client:
            self.logger.warning("Meshtastic client unavailable; skipping ping")
            return True
        if not getattr(meshtastic_client, "myInfo", None):
            self.logger.warning("Meshtastic client myInfo unavailable; skipping ping")
            return True

        my_id = meshtastic_client.myInfo.my_node_num

        if to_id == my_id:
            is_direct_message = True
        elif to_id is None or to_id == BROADCAST_NUM:
            is_direct_message = False
        else:
            return False

        from_id = packet.get("fromId")
        if is_direct_message and not from_id:
            self.logger.warning("Direct message missing fromId; cannot reply")
            return True

        if not self.is_channel_enabled(channel, is_direct_message=is_direct_message):
            return False

        self.logger.info(
            "Processing message from %s on channel %s with plugin '%s'",
            longname,
            channel,
            self.plugin_name,
        )

        if mimic_mode:
            total_punc_length = len(pre_punc) + len(post_punc)

            base_response = match_case(matched_text, "pong")

            reply_message = (
                PING_FALLBACK_RESPONSE
                if total_punc_length > MAX_PUNCTUATION_LENGTH
                else pre_punc + base_response + post_punc
            )
        else:
            reply_message = PING_RESPONSE

        await asyncio.sleep(self.get_response_delay())

        if is_direct_message:
            await asyncio.to_thread(
                meshtastic_client.sendText,
                text=reply_message,
                destinationId=from_id,
            )
        else:
            await asyncio.to_thread(
                meshtastic_client.sendText,
                text=reply_message,
                channelIndex=channel,
            )
        return True

    def get_matrix_commands(self) -> list[str]:
        """
        List the Matrix command names provided by this plugin.

        Returns:
            A list containing the plugin's command name, or an empty list if `plugin_name` is None.
        """
        if self.plugin_name is None:
            return []
        return [self.plugin_name]

    def get_mesh_commands(self) -> list[str]:
        """
        List the mesh command names exposed by this plugin.

        Returns:
            list[str]: Command names provided by the plugin (typically a single-element list containing the plugin's name).
        """
        if self.plugin_name is None:
            return []
        return [self.plugin_name]

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        """
        Reply with the configured ping response in the Matrix room when the event matches this plugin's trigger.

        Parameters:
            room (MatrixRoom): The room containing the event; used to determine the target room_id for the reply.
            event (RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote): The Matrix event to evaluate against the plugin's matching rules.
            full_message (str): The message text (kept for compatibility; not used by this implementation).

        Returns:
            `True` if the event matched and a reply was sent, `False` otherwise.
        """
        # Keep parameter names for compatibility with keyword calls in tests.
        _ = full_message
        if not self.matches(event):
            return False

        await self.send_matrix_message(room.room_id, PING_RESPONSE)
        return True
