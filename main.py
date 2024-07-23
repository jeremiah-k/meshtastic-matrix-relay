"""
This script connects a Meshtastic mesh network to Matrix chat rooms by relaying messages between them.
It uses Meshtastic-python and Matrix nio client library to interface with the radio and the Matrix server respectively.
"""
import asyncio
from nio import (
    RoomMessageText,
    RoomMessageNotice,
)
from pubsub import pub
from typing import List
from db_utils import initialize_database, update_longnames, update_shortnames
from matrix_utils import (
    connect_matrix,
    join_matrix_room,
    on_room_message,
    logger as matrix_logger,
)
from plugin_loader import load_plugins
from config import relay_config
from log_utils import get_logger
from meshtastic_utils import (
    connect_meshtastic,
    on_meshtastic_message,
    on_lost_meshtastic_connection,
    check_connection,
    logger as meshtastic_logger,
)

logger = get_logger(name="M<>M Relay")
meshtastic_interface = connect_meshtastic()
matrix_rooms: List[dict] = relay_config["matrix_rooms"]
matrix_access_token = relay_config["matrix"]["access_token"]

async def main():
    # Initialize the SQLite database
    initialize_database()

    # Load plugins early
    load_plugins()

    matrix_client = await connect_matrix()

    matrix_logger.info("Connecting ...")
    try:
        login_response = await matrix_client.login(matrix_access_token)
    except Exception as e:
        matrix_logger.error(f"Error connecting to Matrix server: {e}")
        return

    # Join the rooms specified in the config.yaml
    for room in matrix_rooms:
        await join_matrix_room(matrix_client, room["id"])

    # Register the Meshtastic message callback
    meshtastic_logger.info(f"Listening for inbound radio messages ...")
    pub.subscribe(
        on_meshtastic_message, "meshtastic.receive", loop=asyncio.get_event_loop()
    )
    pub.subscribe(
        on_lost_meshtastic_connection,
        "meshtastic.connection.lost",
    )
    # Register the message callback
    matrix_logger.info(f"Listening for inbound matrix messages ...")
    matrix_client.add_event_callback(
        on_room_message, (RoomMessageText, RoomMessageNotice)
    )

    # Schedule check_connection
    logger.debug("Scheduling check_connection coroutine")
    asyncio.create_task(check_connection())

    # Start the Matrix client
    while True:
        try:
            if meshtastic_interface:
                # Update longnames & shortnames
                update_longnames(meshtastic_interface.nodes)
                update_shortnames(meshtastic_interface.nodes)

            matrix_logger.info("Syncing with server...")
            await matrix_client.sync_forever(timeout=30000)
            matrix_logger.info("Sync completed.")
        except Exception as e:
            matrix_logger.error(f"Error syncing with server: {e}")

        await asyncio.sleep(60)  # Update longnames & shortnames every 60 seconds

asyncio.run(main())
