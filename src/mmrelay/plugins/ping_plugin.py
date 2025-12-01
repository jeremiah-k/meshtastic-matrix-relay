import asyncio
import os
import re

from meshtastic.mesh_interface import BROADCAST_NUM

from mmrelay.constants.formats import TEXT_MESSAGE_APP
from mmrelay.constants.messages import PORTNUM_TEXT_MESSAGE_APP
from mmrelay.plugins.base_plugin import BasePlugin


def match_case(source: str, target: str) -> str:
    """
    Match the case pattern of the source string and apply it to the target string.

    If lengths differ, the target is truncated to the source length. An empty
    source returns an empty string; an empty target is returned unchanged.

    If the source is all uppercase, make the target all uppercase.
    If the source is all lowercase, make the target all lowercase.
    If the source is title-cased (first letter uppercase, rest lowercase), capitalize the target.
    Otherwise, match the case of each character from the source to the target.

    Args:
        source (str): The string whose case pattern to match.
        target (str): The string to apply the case pattern to.

    Returns:
        str: The target string with the case pattern applied.
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
            t.upper() if s.isupper() else t.lower() for s, t in zip(source, target)
        )


class Plugin(BasePlugin):
    plugin_name = "ping"
    is_core_plugin = True

    @property
    def description(self):
        return "Check connectivity with the relay or respond to pings over the mesh"

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ) -> bool:
        """
        Handle an incoming Meshtastic packet and respond to a matched "ping" message when appropriate.

        Checks packet for decoded text, verifies channel and addressing rules, and if the message contains the word "ping" (optionally surrounded by punctuation) constructs a case-matching "pong" reply and sends it either as a direct message or to the same channel.

        Parameters:
            packet (dict): Meshtastic packet expected to include a `decoded` mapping with `text`, and may include `channel`, `to`, and `fromId`.
            formatted_message (str): Pre-formatted representation of the message (may be unused by this handler).
            longname (str): Human-readable sender identifier used for logging.
            meshnet_name (str): Name of the mesh network where the message originated.

        Returns:
            bool: `True` if the handler processed the packet or intentionally
            suppressed it (for example, when the Meshtastic client or its
            `myInfo` is unavailable), `False` otherwise.
        """
        if "decoded" in packet and "text" in packet["decoded"]:
            portnum = packet["decoded"].get("portnum")
            if portnum is not None and portnum not in (
                TEXT_MESSAGE_APP,
                PORTNUM_TEXT_MESSAGE_APP,
            ):
                return False

            message = packet["decoded"]["text"].strip()
            channel = packet.get("channel", 0)  # Default to channel 0 if not provided

            # Updated regex to match optional punctuation before and after "ping"
            match = re.search(
                r"(?<!\w)([!?]*)(ping)([!?]*)(?!\w)", message, re.IGNORECASE
            )

            if not match:
                return False

            from mmrelay.meshtastic_utils import connect_meshtastic

            if "PYTEST_CURRENT_TEST" in os.environ:
                meshtastic_client = connect_meshtastic()
            else:
                meshtastic_client = await asyncio.to_thread(connect_meshtastic)

            # Determine if the message is a direct message
            toId = packet.get("to")
            if not meshtastic_client:
                self.logger.warning("Meshtastic client unavailable; skipping ping")
                return True
            if not getattr(meshtastic_client, "myInfo", None):
                self.logger.warning(
                    "Meshtastic client myInfo unavailable; skipping ping"
                )
                return True

            myId = meshtastic_client.myInfo.my_node_num  # Get relay's own node number

            if toId == myId:
                # Direct message to us
                is_direct_message = True
            elif toId == BROADCAST_NUM:
                is_direct_message = False
            else:
                # Message to someone else; we may ignore it
                is_direct_message = False

            # Pass is_direct_message to is_channel_enabled
            if not self.is_channel_enabled(
                channel, is_direct_message=is_direct_message
            ):
                # Removed unnecessary logging
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
            base_response = "pong"

            # Adjust base_response to match the case pattern of the matched text
            base_response = match_case(matched_text, base_response)

            # Construct reply message
            if total_punc_length > 5:
                reply_message = "Pong..."
            else:
                reply_message = pre_punc + base_response + post_punc

            # Wait for the response delay
            await asyncio.sleep(self.get_response_delay())

            fromId = packet.get("fromId")

            if is_direct_message:
                # Send reply as DM
                meshtastic_client.sendText(
                    text=reply_message,
                    destinationId=fromId,
                )
            else:
                # Send reply back to the same channel
                meshtastic_client.sendText(text=reply_message, channelIndex=channel)
            return True
        return False  # No decoded text or match, do not process

    def get_matrix_commands(self) -> list[str]:
        """
        Provide Matrix command names exposed by this plugin.

        Returns:
            list[str]: A single-element list containing the plugin's command name.
        """
        return [self.plugin_name]

    def get_mesh_commands(self) -> list[str]:
        return [self.plugin_name]

    async def handle_room_message(self, room, event, full_message) -> bool:
        # Pass event to matches()
        if not self.matches(event):
            return False

        await self.send_matrix_message(room.room_id, "pong!")
        return True
