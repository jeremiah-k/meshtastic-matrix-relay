import asyncio
import re
from typing import Any

from meshtastic.mesh_interface import BROADCAST_NUM  # type: ignore[import-untyped]

from mmrelay.constants.formats import TEXT_MESSAGE_APP
from mmrelay.constants.messages import PORTNUM_TEXT_MESSAGE_APP
from mmrelay.plugins.base_plugin import BasePlugin

# Maximum punctuation length before using shortened response
MAX_PUNCTUATION_LENGTH = 5


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

    @property
    def description(self) -> str:
        """
        Return a short, human-readable description of the plugin's purpose.

        Returns:
            str: Check connectivity with the relay or respond to pings over the mesh
        """
        return "Check connectivity with the relay or respond to pings over the mesh"

    async def handle_meshtastic_message(
        self,
        packet: dict[str, Any],
        formatted_message: str,
        longname: str,
        meshnet_name: str,
    ) -> bool:
        """
        Responds to a "ping" text in an incoming Meshtastic packet with a case-matching "pong" reply when channel/ addressing rules allow.

        Parameters:
            packet (dict[str, Any]): Meshtastic packet expected to include a `decoded` mapping with `text`, and may include `channel`, `to`, and `fromId`.
            formatted_message (str): Pre-formatted representation of the message (may be unused by this handler).
            longname (str): Human-readable sender identifier used for logging.
            meshnet_name (str): Name of the mesh network where the message originated.

        Returns:
            bool: `True` if the handler processed the packet or intentionally suppressed processing (e.g., when the Meshtastic client or its `myInfo` is unavailable), `False` otherwise.
        """
        if "decoded" not in packet or "text" not in packet["decoded"]:
            return False

        portnum = packet["decoded"].get("portnum")
        if portnum is not None and str(portnum) not in {
            str(TEXT_MESSAGE_APP),
            str(PORTNUM_TEXT_MESSAGE_APP),
        }:
            return False

        message = packet["decoded"]["text"].strip()
        channel = packet.get("channel", 0)  # Default to channel 0 if not provided

        # Updated regex to match optional punctuation before and after "ping"
        match = re.search(r"(?<!\w)([!?]*)(ping)([!?]*)(?!\w)", message, re.IGNORECASE)

        if not match:
            return False

        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = await asyncio.to_thread(connect_meshtastic)

        toId = packet.get("to")
        if not meshtastic_client:
            self.logger.warning("Meshtastic client unavailable; skipping ping")
            return True
        if not getattr(meshtastic_client, "myInfo", None):
            self.logger.warning("Meshtastic client myInfo unavailable; skipping ping")
            return True

        myId = meshtastic_client.myInfo.my_node_num  # Get relay's own node number

        if toId == myId:
            # Direct message to us
            is_direct_message = True
        elif toId == BROADCAST_NUM:
            is_direct_message = False
        else:
            # Some radios omit/zero-fill destination; treat as broadcast to avoid dropping valid pings
            is_direct_message = False

        if not self.is_channel_enabled(channel, is_direct_message=is_direct_message):
            return False

        # Log that the plugin is processing the message
        self.logger.info(
            f"Processing message from {longname} on channel {channel} with plugin '{self.plugin_name}'"
        )

        # Extract matched text and punctuation
        pre_punc = match.group(1)
        matched_text = match.group(2)
        post_punc = match.group(3)

        total_punc_length = len(pre_punc) + len(post_punc)

        # Define base response
        base_response = match_case(matched_text, "pong")

        # Construct reply message
        reply_message = (
            "Pong..."
            if total_punc_length > MAX_PUNCTUATION_LENGTH
            else pre_punc + base_response + post_punc
        )

        # Wait for the response delay
        await asyncio.sleep(self.get_response_delay())

        fromId = packet.get("fromId")

        if is_direct_message:
            # Send reply as DM
            await asyncio.to_thread(
                meshtastic_client.sendText,
                text=reply_message,
                destinationId=fromId,
            )
        else:
            # Send reply back to the same channel
            await asyncio.to_thread(
                meshtastic_client.sendText,
                text=reply_message,
                channelIndex=channel,
            )
        return True

    def get_matrix_commands(self) -> list[str]:
        """
        Provide the Matrix command names exposed by this plugin.

        Returns:
            list[str]: A list containing the plugin's Matrix command (the plugin_name).
        """
        return [self.plugin_name]

    def get_mesh_commands(self) -> list[str]:
        """
        List mesh command names exposed by this plugin.

        Returns:
            list[str]: List of command names exposed by the plugin; typically a single-element list containing the plugin's name.
        """
        return [self.plugin_name]

    async def handle_room_message(
        self, room: Any, event: Any, full_message: str
    ) -> bool:
        """
        Reply "pong!" to a Matrix room message that matches this plugin's trigger.

        Parameters:
            room (Any): Matrix room object; used to obtain the target room_id for the reply.
            event (Any): Matrix event to evaluate against the plugin's matching rules.
            full_message (str): The raw or normalized text content of the event.

        Returns:
            True if the message matched and a reply was sent, False otherwise.
        """
        if not self.matches(event):
            return False

        await self.send_matrix_message(room.room_id, "pong!")
        return True
