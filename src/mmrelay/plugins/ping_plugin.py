import asyncio
import re
import string

from meshtastic.mesh_interface import BROADCAST_NUM

from mmrelay.plugins.base_plugin import BasePlugin


def match_case(source, target):
    """
    Apply the letter-case pattern of `source` onto `target`.

    Parameters:
        source (str): String whose uppercase/lowercase pattern will be used.
        target (str): String whose characters will be converted to match `source`'s case.

    Returns:
        str: A new string where each character from `target` is uppercased if the corresponding character in `source` is uppercase, otherwise lowercased. The result length is the minimum of the two input lengths.
    """
    return "".join(
        c.upper() if s.isupper() else c.lower()
        for s, c in zip(source, target, strict=False)
    )


class Plugin(BasePlugin):
    plugin_name = "ping"
    is_core_plugin = True
    punctuation = string.punctuation

    # No __init__ method needed with the simplified plugin system
    # The BasePlugin will automatically use the class-level plugin_name

    @property
    def description(self):
        return "Check connectivity with the relay or respond to pings over the mesh"

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ) -> bool:
        """
        Handle an incoming Meshtastic packet and respond to a matched "ping" message when appropriate.

        Checks the packet for decoded text, verifies channel and addressing rules, and if the message contains the word "ping" (optionally surrounded by punctuation) constructs a case-matching "pong" reply and sends it either as a direct message or to the same channel.

        Parameters:
            packet (dict): Meshtastic packet expected to include a `decoded` mapping with `text`, and may include `channel`, `to`, and `fromId`.
            formatted_message (str): Pre-formatted representation of the message (may be unused by this handler).
            longname (str): Human-readable sender identifier used for logging.
            meshnet_name (str): Name of the mesh network where the message originated.

        Returns:
            bool: `True` if the handler processed the packet and sent a reply, `False` otherwise.
        """
        if "decoded" in packet and "text" in packet["decoded"]:
            message = packet["decoded"]["text"].strip()
            channel = packet.get("channel", 0)  # Default to channel 0 if not provided

            from mmrelay.meshtastic_utils import connect_meshtastic

            meshtastic_client = connect_meshtastic()

            # Determine if the message is a direct message
            toId = packet.get("to")
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
                # Message to someone else; we may ignore it
                is_direct_message = False

            # Pass is_direct_message to is_channel_enabled
            if not self.is_channel_enabled(
                channel, is_direct_message=is_direct_message
            ):
                # Removed unnecessary logging
                return False

            # Updated regex to match optional punctuation before and after "ping"
            match = re.search(
                r"(?<!\w)([!?]*)(ping)([!?]*)(?!\w)", message, re.IGNORECASE
            )
            if match:
                # Log that the plugin is processing the message
                self.logger.info(
                    f"Processing message from {longname} on channel {channel} with plugin '{self.plugin_name}'"
                )

                # Extract matched text and punctuation
                pre_punc = match.group(1)
                matched_text = match.group(2)
                post_punc = match.group(3)

                total_punc_length = len(pre_punc) + len(post_punc)

                # Define the base response
                base_response = "pong"

                # Adjust base_response to match the case pattern of matched_text
                base_response = match_case(matched_text, base_response)

                # Construct the reply message
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
                    # Send the reply back to the same channel
                    meshtastic_client.sendText(text=reply_message, channelIndex=channel)
                return True
            else:
                return False  # No match, do not process

        return False

    def get_matrix_commands(self):
        """
        Provide the Matrix command names exposed by this plugin.

        Returns:
            list[str]: A single-element list containing the plugin's command name.
        """
        return [self.plugin_name]

    def get_mesh_commands(self):
        return [self.plugin_name]

    async def handle_room_message(self, room, event, full_message) -> bool:
        # Pass the event to matches()
        if not self.matches(event):
            return False

        await self.send_matrix_message(room.room_id, "pong!")
        return True
