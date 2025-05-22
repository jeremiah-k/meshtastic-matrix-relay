from mmrelay.plugins.base_plugin import BasePlugin
from mmrelay.matrix_utils import matrix_client, bot_command
from mmrelay.meshtastic_utils import connect_meshtastic

class Plugin(BasePlugin):
    plugin_name = "example_plugin"  # Define plugin_name as a class variable


    async def handle_meshtastic_message(self, packet, formatted_message, longname, meshnet_name):
        # Check if the packet is a TEXT_MESSAGE_APP packet
        if "decoded" in packet and "portnum" in packet["decoded"]:
            if packet["decoded"]["portnum"] == "TEXT_MESSAGE_APP":
                self.logger.debug("Debug logging on Meshtastic TEXT_MESSAGE_APP message")

                # Example of how to get the Meshtastic client
                meshtastic_client = connect_meshtastic()

                # Example of checking if a message is a direct message
                toId = packet.get("to")
                if meshtastic_client and meshtastic_client.myInfo:
                    myId = meshtastic_client.myInfo.my_node_num
                    is_direct_message = (toId == myId)

                    # Example of checking if the channel is enabled for this plugin
                    channel = packet.get("channel", 0)
                    if not self.is_channel_enabled(channel, is_direct_message=is_direct_message):
                        return False

    async def handle_room_message(self, room, event, full_message):
        self.logger.debug("Debug logging on Matrix message")

        # Example of checking if a message is a command for this plugin
        if bot_command(self.plugin_name, event):
            # Example of using the global matrix_client (preferred way)
            if matrix_client is None:
                self.logger.error("Matrix client is not initialized. Cannot send message.")
                return False

            # Example of using the send_matrix_message method (recommended)
            await self.send_matrix_message(
                room_id=room.room_id,
                message="This is a response from the example plugin"
            )
            return True

        return False
