import meshtastic.protobuf.portnums_pb2 # For PortNum enum
from mmrelay.meshtastic.interface import get_meshtastic_interface # To get the client
from mmrelay.log_utils import get_logger

logger = get_logger(name="MeshtasticSender")

async def send_text_to_meshtastic(text: str, channel_index: int = 0):
    """
    Sends a text message to the Meshtastic network.
    This function is now async to allow for potential async operations within.
    """
    meshtastic_iface = get_meshtastic_interface()
    if not meshtastic_iface:
        logger.error("Meshtastic interface not available. Cannot send text message.")
        return None

    logger.debug(f"Attempting to send text to Meshtastic on channel {channel_index}: '{text}'")
    try:
        # meshtastic.MeshInterface.sendText() is blocking.
        # For a truly async operation, it should be run in an executor.
        # loop = asyncio.get_event_loop()
        # sent_packet_info = await loop.run_in_executor(None, meshtastic_iface.sendText, text, channel_index)

        # Direct call for now, assuming caller manages blocking or it's acceptable.
        sent_packet_info = meshtastic_iface.sendText(text=text, channelIndex=channel_index)

        # The return value of sendText can vary. It might be the packet, True, or None.
        # For message mapping, an ID from the sent packet is crucial.
        # If sent_packet_info is a dict-like structure with an 'id', that's useful.
        # This part remains a challenge if the library doesn't consistently return identifiable packet info.
        logger.info(f"Successfully sent text to Meshtastic: '{text}'")
        return sent_packet_info # Return whatever info is available
    except Exception as e:
        logger.error(f"Error sending text message to Meshtastic: {e}", exc_info=True)
        return None


async def send_data_to_meshtastic(data: bytes, channel_index: int = 0, port_num_name: str = "UNKNOWN_APP"):
    """
    Sends a data packet to the Meshtastic network.
    This function is now async.
    """
    meshtastic_iface = get_meshtastic_interface()
    if not meshtastic_iface:
        logger.error("Meshtastic interface not available. Cannot send data packet.")
        return None

    try:
        # Convert port_num_name string to its enum value
        port_num = meshtastic.protobuf.portnums_pb2.PortNum.Value(port_num_name)
    except ValueError:
        logger.error(f"Invalid port_num_name '{port_num_name}'. Cannot send data.")
        return None

    logger.debug(f"Attempting to send data to Meshtastic on channel {channel_index}, port {port_num_name} ({port_num})")
    try:
        # Similar to sendText, sendData is blocking.
        # Consider executor for async callers if blocking is an issue.
        # sent_packet_info = await asyncio.get_event_loop().run_in_executor(None, meshtastic_iface.sendData, data, channel_index, port_num)

        sent_packet_info = meshtastic_iface.sendData(
            data=data, # Should be bytes
            channelIndex=channel_index,
            portNum=port_num,
            # wantAck=False, # Optional: request acknowledgment
            # wantResponse=False # Optional: request response
        )
        logger.info(f"Successfully sent data to Meshtastic port {port_num_name}.")
        return sent_packet_info
    except Exception as e:
        logger.error(f"Error sending data packet to Meshtastic: {e}", exc_info=True)
        return None
