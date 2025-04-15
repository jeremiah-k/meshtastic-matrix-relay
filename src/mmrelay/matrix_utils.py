import asyncio
import io
import json
import os
import re
import ssl
import time
from typing import Union
from urllib.parse import urlparse

import certifi
import meshtastic.protobuf.portnums_pb2
from nio import (
    AsyncClient,
    AsyncClientConfig,
    MatrixRoom,
    MegolmEvent,
    ReactionEvent,
    RoomEncryptionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
    UploadResponse,
    WhoamiError,
    exceptions,
)
from PIL import Image

from mmrelay.config import get_e2ee_store_dir
from mmrelay.db_utils import (
    get_message_map_by_matrix_event_id,
    prune_message_map,
    store_message_map,
)
from mmrelay.log_utils import get_logger

# Do not import plugin_loader here to avoid circular imports
from mmrelay.meshtastic_utils import connect_meshtastic

# Global config variable that will be set from config.py
config = None

# These will be set in connect_matrix()
matrix_homeserver = None
matrix_rooms = None
matrix_access_token = None
bot_user_id = None
bot_user_name = None  # Detected upon logon
bot_start_time = int(
    time.time() * 1000
)  # Timestamp when the bot starts, used to filter out old messages

logger = get_logger(name="Matrix")

matrix_client = None


def bot_command(command, event):
    """
    Checks if the given command is directed at the bot,
    accounting for variations in different Matrix clients.
    """
    full_message = event.body.strip()
    content = event.source.get("content", {})
    formatted_body = content.get("formatted_body", "")

    # Remove HTML tags and extract the text content
    text_content = re.sub(r"<[^>]+>", "", formatted_body).strip()

    # Check for simple !command format first
    if full_message.startswith(f"!{command}") or text_content.startswith(f"!{command}"):
        return True

    # Check if the message starts with bot_user_id or bot_user_name
    if full_message.startswith(bot_user_id) or text_content.startswith(bot_user_id):
        # Construct a regex pattern to match variations of bot mention and command
        pattern = rf"^(?:{re.escape(bot_user_id)}|{re.escape(bot_user_name)}|[#@].+?)[,:;]?\s*!{command}"
        return bool(re.match(pattern, full_message)) or bool(
            re.match(pattern, text_content)
        )
    elif full_message.startswith(bot_user_name) or text_content.startswith(
        bot_user_name
    ):
        # Construct a regex pattern to match variations of bot mention and command
        pattern = rf"^(?:{re.escape(bot_user_id)}|{re.escape(bot_user_name)}|[#@].+?)[,:;]?\s*!{command}"
        return bool(re.match(pattern, full_message)) or bool(
            re.match(pattern, text_content)
        )
    else:
        return False


async def connect_matrix(passed_config=None):
    """
    Establish a connection to the Matrix homeserver.
    Sets global matrix_client and detects the bot's display name.

    Args:
        passed_config: The configuration dictionary to use (will update global config)
    """
    global matrix_client, bot_user_name, matrix_homeserver, matrix_rooms, matrix_access_token, bot_user_id, config

    # Update the global config if a config is passed
    if passed_config is not None:
        config = passed_config

    # Check for credentials.json first
    credentials = None
    credentials_path = None

    # Try to find credentials.json in the config directory
    try:
        from mmrelay.config import get_base_dir
        config_dir = get_base_dir()
        credentials_path = os.path.join(config_dir, "credentials.json")

        if os.path.exists(credentials_path):
            logger.info(f"Found credentials at {credentials_path}")
            with open(credentials_path, "r") as f:
                credentials = json.load(f)
    except Exception as e:
        logger.warning(f"Error loading credentials: {e}")

    # If credentials.json exists, use it
    if credentials:
        matrix_homeserver = credentials["homeserver"]
        matrix_access_token = credentials["access_token"]
        bot_user_id = credentials["user_id"]
        e2ee_device_id = credentials["device_id"]
        logger.info(f"Using credentials from {credentials_path}")
        # If config also has Matrix login info, let the user know we're ignoring it
        if config and "matrix" in config and "access_token" in config["matrix"]:
            logger.info("NOTE: Ignoring Matrix login details in config.yaml in favor of credentials.json")
    else:
        # Check if config is available
        if config is None:
            logger.error("No configuration available. Cannot connect to Matrix.")
            return None

        # Extract Matrix configuration from config
        matrix_homeserver = config["matrix"]["homeserver"]
        matrix_access_token = config["matrix"]["access_token"]
        bot_user_id = config["matrix"]["bot_user_id"]

    # Get matrix rooms from config
    matrix_rooms = config["matrix_rooms"]

    # Check if client already exists
    if matrix_client:
        return matrix_client

    # Create SSL context using certifi's certificates
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    # Check if E2EE is enabled
    e2ee_enabled = False
    e2ee_store_path = None
    e2ee_device_id = None

    try:
        # Check both 'encryption' and 'e2ee' keys for backward compatibility
        if (("encryption" in config["matrix"] and config["matrix"]["encryption"].get("enabled", False)) or
            ("e2ee" in config["matrix"] and config["matrix"]["e2ee"].get("enabled", False))):
            # Check if python-olm is installed
            try:
                import olm  # noqa: F401

                e2ee_enabled = True
                logger.info("End-to-End Encryption (E2EE) is enabled")

                # Get store path from config or use default
                if "encryption" in config["matrix"] and "store_path" in config["matrix"]["encryption"]:
                    e2ee_store_path = os.path.expanduser(
                        config["matrix"]["encryption"]["store_path"]
                    )
                elif "e2ee" in config["matrix"] and "store_path" in config["matrix"]["e2ee"]:
                    e2ee_store_path = os.path.expanduser(
                        config["matrix"]["e2ee"]["store_path"]
                    )
                else:
                    from mmrelay.config import get_e2ee_store_dir
                    e2ee_store_path = get_e2ee_store_dir()

                # Create store directory if it doesn't exist
                os.makedirs(e2ee_store_path, exist_ok=True)

                # Check if store directory contains database files
                store_files = os.listdir(e2ee_store_path) if os.path.exists(e2ee_store_path) else []
                db_files = [f for f in store_files if f.endswith('.db')]
                if db_files:
                    logger.info(f"Found existing E2EE store files: {', '.join(db_files)}")
                else:
                    logger.warning("No existing E2EE store files found. Encryption may not work correctly.")

                logger.info(f"Using E2EE store path: {e2ee_store_path}")

                # We'll get the device ID from whoami() later
                e2ee_device_id = None
                logger.debug("Will retrieve device_id from whoami() response")
            except ImportError:
                logger.warning(
                    "E2EE is enabled in config but python-olm is not installed."
                )
                logger.warning("Install mmrelay[e2e] to use E2EE features.")
                e2ee_enabled = False
    except (KeyError, TypeError):
        # E2EE not configured
        pass

    # Initialize the Matrix client with custom SSL context
    client_config = AsyncClientConfig(
        encryption_enabled=e2ee_enabled, store_sync_tokens=True
    )

    # Log the device ID being used
    if e2ee_device_id:
        logger.info(f"Using device ID: {e2ee_device_id}")

    matrix_client = AsyncClient(
        homeserver=matrix_homeserver,
        user=bot_user_id,
        device_id=e2ee_device_id,  # Will be None if not specified in config or credentials
        store_path=e2ee_store_path if e2ee_enabled else None,
        config=client_config,
        ssl=ssl_context,
    )

    # Set the access_token and user_id
    matrix_client.access_token = matrix_access_token
    matrix_client.user_id = bot_user_id

    # =====================================================================
    # MATRIX E2EE IMPLEMENTATION - CRITICAL SEQUENCE
    # =====================================================================
    # The sequence of operations is critical for proper E2EE functionality:
    #
    # 1. First, perform an early lightweight sync to initialize rooms and subscriptions
    #    This is critical for message delivery to work properly. Without this sync,
    #    the client would have no known rooms when messages are sent, causing silent failures.
    #    This sync populates matrix_client.rooms with the room objects needed for message delivery.
    #
    # 2. After this initial sync, we'll retrieve the device_id, verify credentials,
    #    load the encryption store, and upload keys BEFORE the main sync.
    #
    # 3. Only then will we perform the main sync that updates encryption state.
    #
    # This sequence ensures both proper message delivery AND encryption.
    # =====================================================================

    # Step 1: Early lightweight sync to initialize rooms and subscriptions
    logger.info("Performing early lightweight sync to initialize rooms...")
    await matrix_client.sync(timeout=2000)
    logger.info(f"Early sync completed with {len(matrix_client.rooms)} rooms")

    # Retrieve the device_id using whoami() - this is critical for E2EE
    whoami_response = await matrix_client.whoami()
    if isinstance(whoami_response, WhoamiError):
        logger.error(f"Failed to retrieve device_id: {whoami_response.message}")
        if e2ee_enabled:
            logger.error(
                "E2EE requires a valid device_id. E2EE may not work correctly."
            )
        # Don't reset the device_id to None if we already have one from credentials
        if not matrix_client.device_id:
            logger.error("No device_id available. E2EE will not work correctly.")
    else:
        # Check if the device_id from whoami matches our credentials
        server_device_id = whoami_response.device_id
        if server_device_id:
            if not matrix_client.device_id:
                # If we don't have a device_id from credentials, use the one from whoami
                matrix_client.device_id = server_device_id
                logger.info(f"Using device_id from server: {matrix_client.device_id}")

                # Update credentials.json with the correct device_id
                if credentials and credentials_path:
                    try:
                        credentials["device_id"] = server_device_id
                        with open(credentials_path, "w") as f:
                            json.dump(credentials, f)
                        logger.info(f"Updated credentials.json with device_id: {server_device_id}")
                    except Exception as e:
                        logger.warning(f"Failed to update credentials.json: {e}")
            elif matrix_client.device_id != server_device_id:
                # If the device_id from credentials doesn't match the server, update it
                logger.warning(f"Device ID mismatch: credentials={matrix_client.device_id}, server={server_device_id}")
                logger.info(f"Updating device_id to match server: {server_device_id}")
                matrix_client.device_id = server_device_id

                # Update credentials.json with the correct device_id
                if credentials and credentials_path:
                    try:
                        credentials["device_id"] = server_device_id
                        with open(credentials_path, "w") as f:
                            json.dump(credentials, f)
                        logger.info(f"Updated credentials.json with device_id: {server_device_id}")
                    except Exception as e:
                        logger.warning(f"Failed to update credentials.json: {e}")
            else:
                # Device IDs match, all good
                logger.info(f"Using verified device_id: {matrix_client.device_id}")
        else:
            logger.error(
                "No device_id returned by whoami(). E2EE will not work correctly."
            )

    # Fetch the bot's display name
    response = await matrix_client.get_displayname(bot_user_id)
    if hasattr(response, "displayname"):
        bot_user_name = response.displayname
    else:
        bot_user_name = bot_user_id  # Fallback if display name is not set

    # =====================================================================
    # MATRIX E2EE IMPLEMENTATION - ENCRYPTION SETUP
    # =====================================================================
    # This section handles the critical encryption setup sequence:
    # 1. Load the encryption store (contains keys and device information)
    # 2. Upload keys BEFORE performing the main sync
    # 3. Perform sync AFTER key upload
    # 4. Verify that rooms are properly populated
    #
    # This sequence is critical for proper encryption. If keys are not uploaded
    # before the first sync that handles encrypted messages, the first message
    # will fail to encrypt properly ("waiting for this message" error in Element).
    # =====================================================================

    # If E2EE is enabled, load the store and set up encryption
    if e2ee_enabled:
        try:
            # Check if store directory contains database files
            store_files = os.listdir(e2ee_store_path) if os.path.exists(e2ee_store_path) else []
            db_files = [f for f in store_files if f.endswith('.db')]
            if db_files:
                logger.info(f"Found existing E2EE store files: {', '.join(db_files)}")
            else:
                logger.warning("No existing E2EE store files found. Encryption may not work correctly.")

            # Load the store first
            logger.debug("Loading encryption store...")
            matrix_client.load_store()
            logger.debug("Encryption store loaded successfully")

            # Debug store state
            logger.debug(f"E2EE store path: {e2ee_store_path}")
            logger.debug(f"Device store users immediately after load: {list(matrix_client.device_store.users) if matrix_client.device_store else 'None'}")

            # Confirm client credentials are set
            logger.debug(f"Checking client credentials: user_id={matrix_client.user_id}, device_id={matrix_client.device_id}")
            if not (matrix_client.user_id and matrix_client.device_id and matrix_client.access_token):
                logger.warning("Missing essential credentials for E2EE. Encryption may not work correctly.")

            # Upload keys BEFORE first sync
            logger.debug("Uploading encryption keys to server BEFORE sync")
            try:
                if matrix_client.should_upload_keys:
                    await matrix_client.keys_upload()
                    logger.debug("Encryption keys uploaded successfully")
                else:
                    logger.debug("No key upload needed at this stage")
            except Exception as ke:
                logger.warning(f"Error uploading keys: {ke}")

            # Now perform sync after keys are uploaded
            logger.debug("Performing sync AFTER key upload")
            await matrix_client.sync(timeout=5000)

            # Debug store state after sync
            logger.debug(f"Device store users after sync: {list(matrix_client.device_store.users) if matrix_client.device_store else 'None'}")

            # Verify that rooms are properly populated
            if not matrix_client.rooms:
                logger.warning("No rooms found after sync. Message delivery may not work correctly.")
            else:
                # Only log room info at debug level
                logger.debug(f"Found {len(matrix_client.rooms)} rooms after sync")
                logger.debug(f"Available rooms: {list(matrix_client.rooms.keys())}")

            # Trust all of our own devices to ensure encryption works
            logger.debug("Setting up cross-signing for encryption...")
            try:
                # First make sure we have synced to populate the device store
                logger.debug("Performing sync to populate device store...")
                await matrix_client.sync(timeout=5000)

                # Check if our user_id is in the device_store
                if matrix_client.device_store and matrix_client.user_id in matrix_client.device_store:
                    devices = matrix_client.device_store[matrix_client.user_id]
                    logger.info(f"Found {len(devices)} of our own devices in the device store")

                    # Trust other devices but NOT our current one
                    # Matrix cannot verify its own current device directly
                    for device_id, device in devices.items():
                        # Skip our current device as we can't verify it directly
                        # This is a fundamental limitation in Matrix's trust model
                        if device_id == matrix_client.device_id:
                            logger.debug(f"Skipping verification of our current device: {device_id} (cannot verify own device)")
                            continue

                        try:
                            # Verify other devices
                            matrix_client.verify_device(device)
                            logger.info(f"Trusted own device {device_id}")
                        except Exception as e:
                            logger.debug(f"Failed to trust device {device_id}: {e}")

                    # Log about our current device
                    if matrix_client.device_id in devices:
                        logger.info(f"Our current device is in the device store: {matrix_client.device_id}")
                    else:
                        logger.debug(f"Our current device {matrix_client.device_id} not found in device store (this is normal)")
                else:
                    logger.debug("No devices found for our user in the device store (this is normal for first run)")
            except Exception as ve:
                logger.warning(f"Error verifying devices: {ve}")

            # Patch the client to handle unverified devices
            # This is a safer approach than monkey patching the OlmDevice class
            original_encrypt_for_devices = None
            if hasattr(matrix_client, "olm") and matrix_client.olm:
                if hasattr(matrix_client.olm, "encrypt_for_devices"):
                    original_encrypt_for_devices = matrix_client.olm.encrypt_for_devices

                    # Create a wrapper that ignores verification status
                    async def encrypt_for_all_devices(
                        room_id, users_devices, plaintext
                    ):
                        # Force ignore_unverified_devices=True
                        return await original_encrypt_for_devices(
                            room_id,
                            users_devices,
                            plaintext,
                            ignore_unverified_devices=True,
                        )

                    # Apply the patch
                    matrix_client.olm.encrypt_for_devices = encrypt_for_all_devices
                    logger.debug(
                        "Patched encrypt_for_devices to ignore verification status"
                    )

            # Verify all devices in the store
            if (
                matrix_client.device_store
                and matrix_client.olm
                and matrix_client.olm.store
            ):
                verified_count = 0

                # First, make sure our own device is verified and trusted
                if matrix_client.device_id and matrix_client.user_id:
                    logger.debug(
                        "Setting up cross-signing for our devices"
                    )
                    try:
                        # Get our own devices
                        own_devices = matrix_client.device_store.active_user_devices(
                            matrix_client.user_id
                        )
                        for device in own_devices:
                            # Skip our current device as we can't verify it directly
                            # This is a fundamental limitation in Matrix's trust model
                            if device.device_id == matrix_client.device_id:
                                logger.debug(f"Skipping verification of our current device: {device.device_id} (cannot verify own device)")
                                continue

                            # Verify other devices
                            matrix_client.verify_device(device)

                            # Also mark the device as trusted if that method exists
                            if hasattr(matrix_client.olm.store, "mark_device_as_trusted"):
                                matrix_client.olm.store.mark_device_as_trusted(device)

                            logger.debug(
                                f"Verified and trusted other device: {device.device_id}"
                            )
                            verified_count += 1
                    except Exception as e:
                        logger.debug(f"Error verifying other devices: {e}")

                # Then verify and trust all other devices
                for user_id in matrix_client.device_store.users:
                    # Skip our own user as we already processed it
                    if user_id == matrix_client.user_id:
                        continue

                    for device in matrix_client.device_store.active_user_devices(
                        user_id
                    ):
                        # Always verify and trust all devices
                        matrix_client.olm.store.verify_device(device)
                        # Also mark the device as trusted in the store
                        if hasattr(matrix_client.olm.store, "mark_device_as_trusted"):
                            matrix_client.olm.store.mark_device_as_trusted(device)
                        verified_count += 1

                if verified_count > 0:
                    logger.debug(
                        f"Verified and trusted {verified_count} total devices in the store"
                    )

            # Upload keys if needed
            if matrix_client.should_upload_keys:
                logger.debug("Uploading encryption keys to server")
                try:
                    await matrix_client.keys_upload()
                    logger.debug("Keys uploaded successfully")
                except Exception as ke:
                    logger.warning(f"Error uploading keys: {ke}")

            logger.debug("E2EE setup complete - will encrypt for all devices")

        except Exception as e:
            logger.error(f"Error setting up E2EE: {e}")
            # Continue without E2EE if there's an error

    return matrix_client


async def join_matrix_room(matrix_client, room_id_or_alias: str) -> None:
    """Join a Matrix room by its ID or alias."""
    try:
        if room_id_or_alias.startswith("#"):
            # If it's a room alias, resolve it to a room ID
            response = await matrix_client.room_resolve_alias(room_id_or_alias)
            if not response.room_id:
                logger.error(
                    f"Failed to resolve room alias '{room_id_or_alias}': {response.message}"
                )
                return
            room_id = response.room_id
            # Update the room ID in the matrix_rooms list
            for room_config in matrix_rooms:
                if room_config["id"] == room_id_or_alias:
                    room_config["id"] = room_id
                    break
        else:
            room_id = room_id_or_alias

        # Attempt to join the room if not already joined
        if room_id not in matrix_client.rooms:
            response = await matrix_client.join(room_id)
            if response and hasattr(response, "room_id"):
                logger.info(f"Joined room '{room_id_or_alias}' successfully")

                # Force a sync to update the client's rooms list
                logger.debug(f"Forcing sync after joining room {room_id}")
                await matrix_client.sync(
                    timeout=5000
                )  # Increased timeout for better reliability

                # If the room is still not in the client's rooms after sync, try to get room state
                if room_id not in matrix_client.rooms:
                    logger.debug(
                        f"Room {room_id} not in client's rooms after sync. Trying to get room state..."
                    )
                    try:
                        state = await matrix_client.room_get_state(room_id)
                        logger.debug(
                            f"Got room state for {room_id}, events: {len(state.events)}"
                        )
                        # Force another sync
                        await matrix_client.sync(
                            timeout=5000
                        )  # Increased timeout for better reliability
                    except Exception as state_error:
                        logger.warning(f"Error getting room state: {state_error}")

                # If the room is still not in the client's rooms, create it manually
                if room_id not in matrix_client.rooms:
                    logger.debug(
                        f"Room {room_id} still not in client's rooms. Creating room object manually..."
                    )
                    # Create a minimal room object
                    from nio import MatrixRoom

                    matrix_client.rooms[room_id] = MatrixRoom(
                        room_id, matrix_client.user_id
                    )
                    logger.debug(f"Manually created room object for {room_id}")
            else:
                logger.error(
                    f"Failed to join room '{room_id_or_alias}': {response.message}"
                )
        else:
            logger.debug(f"Bot is already in room '{room_id_or_alias}'")
    except Exception as e:
        logger.error(f"Error joining room '{room_id_or_alias}': {e}")


async def matrix_relay(
    room_id,
    message,
    longname,
    shortname,
    meshnet_name,
    portnum,
    meshtastic_id=None,
    meshtastic_replyId=None,
    meshtastic_text=None,
    emote=False,
    emoji=False,
):
    """
    Relay a message from Meshtastic to Matrix, optionally storing message maps.

    IMPORTANT CHANGE: Now, we only store message maps if `relay_reactions` is True.
    If `relay_reactions` is False, we skip storing to the message map entirely.
    This helps maintain privacy and prevents message_map usage unless needed.

    Additionally, if `msgs_to_keep` > 0, we prune the oldest messages after storing
    to prevent database bloat and maintain privacy.
    """
    global config

    # Log the current state of the config
    logger.debug(f"matrix_relay: config is {'available' if config else 'None'}")

    # Use the global matrix_client instead of creating a new one
    global matrix_client

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot relay message to Matrix.")
        return

    # Retrieve relay_reactions configuration; default to False now if not specified.
    relay_reactions = config["meshtastic"].get("relay_reactions", False)

    # Retrieve db config for message_map pruning
    # Check database config for message map settings (preferred format)
    database_config = config.get("database", {})
    msg_map_config = database_config.get("msg_map", {})

    # If not found in database config, check legacy db config
    if not msg_map_config:
        db_config = config.get("db", {})
        legacy_msg_map_config = db_config.get("msg_map", {})

        if legacy_msg_map_config:
            msg_map_config = legacy_msg_map_config
            logger.warning(
                "Using 'db.msg_map' configuration (legacy). 'database.msg_map' is now the preferred format and 'db.msg_map' will be deprecated in a future version."
            )
    msgs_to_keep = msg_map_config.get(
        "msgs_to_keep", 500
    )  # Default is 500 if not specified

    try:
        # Always use our own local meshnet_name for outgoing events
        local_meshnet_name = config["meshtastic"]["meshnet_name"]
        content = {
            "msgtype": "m.text" if not emote else "m.emote",
            "body": message,
            "meshtastic_longname": longname,
            "meshtastic_shortname": shortname,
            "meshtastic_meshnet": local_meshnet_name,
            "meshtastic_portnum": portnum,
        }
        if meshtastic_id is not None:
            content["meshtastic_id"] = meshtastic_id
        if meshtastic_replyId is not None:
            content["meshtastic_replyId"] = meshtastic_replyId
        if meshtastic_text is not None:
            content["meshtastic_text"] = meshtastic_text
        if emoji:
            content["meshtastic_emoji"] = 1

        try:
            # Ensure matrix_client is not None
            if not matrix_client:
                logger.error("Matrix client is None. Cannot send message.")
                return

            # Ensure the room exists and we're joined to it
            if room_id not in matrix_client.rooms:
                logger.warning(
                    f"Room {room_id} not found in joined rooms. Attempting to join..."
                )
                try:
                    # Try to join the room
                    response = await matrix_client.join(room_id)
                    if not hasattr(response, "room_id"):
                        logger.error(
                            f"Failed to join room {room_id}: {getattr(response, 'message', 'Unknown error')}"
                        )
                        return
                    logger.info(f"Successfully joined room {room_id}")

                    # Important: Wait for a sync to complete so the room is fully loaded
                    logger.debug(
                        f"Waiting for sync to complete after joining room {room_id}"
                    )
                    await matrix_client.sync(
                        timeout=15000
                    )  # Increased timeout for better reliability

                    # Log the rooms we have after sync
                    logger.debug(f"Rooms after sync: {list(matrix_client.rooms.keys())}")

                    # Force the room to be added to the client's rooms if it's not there yet
                    # This is a workaround for the sync not always adding the room
                    if room_id not in matrix_client.rooms:
                        logger.debug(
                            f"Room {room_id} not in client's rooms after sync. Trying to get room state..."
                        )
                        try:
                            # Try to get the room state to force the room to be added
                            state = await matrix_client.room_get_state(room_id)
                            if hasattr(state, "events"):
                                logger.debug(
                                    f"Got room state for {room_id}, events: {len(state.events)}"
                                )
                                # Force another sync
                                await matrix_client.sync(timeout=5000)
                        except Exception as state_error:
                            logger.warning(f"Error getting room state: {state_error}")

                    # If the room is still not in the client's rooms, create it manually
                    if room_id not in matrix_client.rooms:
                        logger.debug(
                            f"Room {room_id} still not in client's rooms. Creating room object manually..."
                        )
                        # Create a minimal room object
                        from nio import MatrixRoom

                        matrix_client.rooms[room_id] = MatrixRoom(
                            room_id, matrix_client.user_id
                        )
                        logger.debug(f"Created manual room object for {room_id}")

                    logger.debug(f"Room {room_id} successfully processed")
                except Exception as e:
                    logger.error(f"Error joining room {room_id}: {e}")
                    return

            # Check if the room is encrypted
            room = matrix_client.rooms.get(room_id)
            is_encrypted = room and room.encrypted

            # Debug room encryption status
            if room:
                logger.debug(f"Room {room_id} encryption status: {room.encrypted}")
            else:
                logger.debug(f"Room {room_id} not found in client's rooms")

            # =====================================================================
            # MATRIX E2EE IMPLEMENTATION - MESSAGE ENCRYPTION
            # =====================================================================
            # This section handles the encryption of outgoing messages:
            # 1. Verify all devices in the room to ensure encryption works
            # 2. Load the encryption store to ensure keys are available
            # 3. Upload keys BEFORE sharing group session
            # 4. Share group session for the room
            # 5. Perform sync AFTER sharing group session
            # 6. Send the encrypted message
            #
            # This sequence is critical for proper message encryption. If any step
            # is skipped or performed out of order, messages may fail to encrypt
            # properly or may not be decryptable by recipients.
            # =====================================================================

            if is_encrypted:
                logger.debug(f"Room {room_id} is encrypted, sending with encryption")

                # Make sure we have a group session for this room
                try:
                    # Ensure we have shared a group session
                    if matrix_client.olm:
                        # First, trust our other devices (not our current one)
                        logger.debug("Setting up cross-signing before sending encrypted message...")
                        try:
                            # Check if our user_id is in the device_store
                            if matrix_client.device_store and matrix_client.user_id in matrix_client.device_store:
                                devices = matrix_client.device_store[matrix_client.user_id]

                                # Trust other devices but NOT our current one
                                # Matrix cannot verify its own current device directly
                                for device_id, device in devices.items():
                                    # Skip our current device as we can't verify it directly
                                    # This is a fundamental limitation in Matrix's trust model
                                    if device_id == matrix_client.device_id:
                                        logger.debug(f"Skipping verification of our current device: {device_id} (cannot verify own device)")
                                        continue

                                    try:
                                        # Verify other devices
                                        matrix_client.verify_device(device)
                                        logger.debug(f"Trusted own device {device_id}")

                                        # Also mark the device as trusted if that method exists
                                        if hasattr(matrix_client.olm.store, "mark_device_as_trusted"):
                                            matrix_client.olm.store.mark_device_as_trusted(device)
                                    except Exception as e:
                                        logger.warning(f"Failed to trust device {device_id}: {e}")
                            else:
                                logger.debug("No devices found for our user in the device store (this is normal for first run)")
                        except Exception as ve:
                            logger.debug(f"Error verifying other devices: {ve}")

                        # Then, verify ALL other devices in the room to ensure encryption works
                        logger.debug(f"Verifying all devices in room {room_id}")
                        if matrix_client.device_store:
                            for user_id in room.users:
                                if user_id == matrix_client.user_id:  # Skip our own user
                                    continue

                                # Get all devices for this user and verify them
                                for device in matrix_client.device_store.active_user_devices(user_id):
                                    # Verify the device using the client's method
                                    matrix_client.verify_device(device)
                                    logger.debug(f"Verified device {device.device_id} for user {user_id}")

                        # Make sure the store is loaded
                        try:
                            matrix_client.load_store()
                            logger.debug("Encryption store loaded successfully")
                        except Exception as le:
                            logger.warning(f"Error loading encryption store: {le}")

                        # Debug device store state
                        logger.debug(f"Device store users before key operations: {list(matrix_client.device_store.users) if matrix_client.device_store else 'None'}")

                        # Upload keys BEFORE sync
                        logger.debug("Uploading encryption keys BEFORE sync")
                        try:
                            if matrix_client.should_upload_keys:
                                await matrix_client.keys_upload()
                                logger.debug("Keys uploaded successfully")
                            else:
                                logger.debug("No key upload needed before sending message")
                        except Exception as ke:
                            logger.warning(f"Error uploading keys: {ke}")

                        # Perform sync AFTER key upload
                        logger.debug("Performing sync AFTER key upload")
                        await matrix_client.sync(timeout=3000)

                        # Build a list of all devices in the room
                        users_devices = {}
                        for user_id in room.users:
                            if user_id != matrix_client.user_id:  # Skip our own user
                                # Get all devices for this user
                                devices = matrix_client.device_store.active_user_devices(user_id)
                                if devices:
                                    users_devices[user_id] = [device.device_id for device in devices]

                        # Debug users_devices
                        logger.debug(f"Users devices before key claim: {users_devices}")

                        # Claim keys for all devices
                        if users_devices:
                            logger.debug(f"Claiming keys for {len(users_devices)} users in room {room_id}")
                            try:
                                await matrix_client.keys_claim(users_devices)
                                logger.debug("Keys claimed successfully")
                            except Exception as ke:
                                logger.warning(f"Error claiming keys: {ke}")
                        else:
                            logger.debug("No devices found for keys_claim, skipping attempt")

                        # Force sharing a new group session for this room
                        logger.debug(f"Sharing new group session for room {room_id}")
                        try:
                            # Make sure the store is loaded
                            try:
                                matrix_client.load_store()
                                logger.debug("Encryption store loaded successfully")
                            except Exception as le:
                                logger.warning(f"Error loading encryption store: {le}")

                            # Implement exponential backoff retry for sharing group session
                            import asyncio
                            max_attempts = 3
                            for attempt in range(max_attempts):
                                try:
                                    # Always use ignore_unverified_devices=True to ensure messages can be sent
                                    await matrix_client.share_group_session(room_id, ignore_unverified_devices=True)
                                    logger.debug(f"Shared new group session for room {room_id} on attempt {attempt + 1}")
                                    break
                                except Exception as share_error:
                                    error_str = str(share_error)
                                    logger.warning(f"Group session sharing failed attempt {attempt + 1}: {error_str}")

                                    # If we're already sharing a group session, that's actually fine
                                    # We can just continue without retrying
                                    if "Already sharing a group session" in error_str:
                                        logger.debug(f"Group session already being shared for room {room_id}, continuing")
                                        break

                                    if attempt < max_attempts - 1:  # Don't sleep on the last attempt
                                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                                    else:
                                        raise  # Re-raise on last attempt

                            # Perform a short sync to ensure the group session is properly registered
                            logger.debug("Performing short sync to update encryption state...")
                            await matrix_client.sync(timeout=3000)  # 3 second timeout

                        except Exception as share_error:
                            error_str = str(share_error)
                            logger.error(f"Error sharing group session: {error_str}")

                            # If we're already sharing a group session, that's actually fine
                            # We can just continue without recovery
                            if "Already sharing a group session" in error_str:
                                logger.debug(f"Group session already being shared for room {room_id}, continuing without recovery")
                            else:
                                # If sharing fails for other reasons, try to recover by forcing a new upload and share
                                try:
                                    logger.debug("Attempting recovery by uploading keys again...")
                                    await matrix_client.keys_upload()

                                    # Try to share with retry for Already sharing errors
                                    try:
                                        await matrix_client.share_group_session(room_id, ignore_unverified_devices=True)
                                    except Exception as retry_error:
                                        if "Already sharing a group session" in str(retry_error):
                                            logger.debug("Recovery detected group session already being shared, continuing")
                                        else:
                                            raise

                                    # Perform a short sync to ensure the group session is properly registered
                                    logger.debug("Performing short sync to update encryption state...")
                                    await matrix_client.sync(timeout=3000)  # 3 second timeout

                                    logger.debug("Recovery successful, shared new group session")
                                except Exception as recovery_error:
                                    logger.error(f"Recovery failed: {recovery_error}")
                except Exception as e:
                    logger.error(f"Error preparing encryption: {e}")
            else:
                logger.debug(f"Room {room_id} is not encrypted")

            # Send the message with a timeout and retry logic
            max_retries = 3
            response = None

            for attempt in range(1, max_retries + 1):
                try:
                    response = await asyncio.wait_for(
                        matrix_client.room_send(
                            room_id=room_id,
                            message_type="m.room.message",
                            content=content,
                            ignore_unverified_devices=True,  # Important: ignore unverified devices
                        ),
                        timeout=10.0,  # Increased timeout
                    )
                    logger.debug(f"Message sent successfully to room {room_id} on attempt {attempt}")
                    break
                except Exception as send_error:
                    error_str = str(send_error)
                    logger.warning(f"Error sending message to room {room_id} on attempt {attempt}: {error_str}")

                    # If we're already sharing a group session, wait a bit and retry
                    if "Already sharing a group session" in error_str and attempt < max_retries:
                        logger.debug(f"Group session already being shared for room {room_id}, waiting before retry")
                        await asyncio.sleep(1.5 * attempt)  # Backoff
                        continue
                    elif attempt < max_retries:
                        # For other errors, try a sync and retry
                        logger.debug("Attempting recovery by forcing a sync...")
                        await matrix_client.sync(timeout=3000)
                        await asyncio.sleep(1.0 * attempt)  # Backoff
                        continue
                    else:
                        # Re-raise on last attempt
                        raise

            # Log at info level, matching one-point-oh pattern
            logger.info(f"Sent inbound radio message to matrix room: {room_id}")
            # Additional details at debug level
            if hasattr(response, "event_id"):
                logger.debug(f"Message event_id: {response.event_id}")

        except asyncio.TimeoutError:
            logger.error(f"Timeout sending message to Matrix room {room_id}")
            return
        except exceptions.OlmUnverifiedDeviceError as e:
            # This should not happen with our patches, but just in case
            logger.warning(
                f"Encryption error with unverified device in room {room_id}: {e}"
            )
            logger.warning(
                "Attempting to verify all devices and share a new group session..."
            )
            try:
                # Get all devices in the room and mark them as verified
                if matrix_client.olm and matrix_client.device_store:
                    # Get all users in the room
                    room = matrix_client.rooms.get(room_id)
                    if room:
                        # Verify all devices for all users in the room
                        for user_id in room.users:
                            # Skip our own user
                            if user_id == matrix_client.user_id:
                                continue

                            # Get all devices for this user and mark them as verified
                            for (
                                device
                            ) in matrix_client.device_store.active_user_devices(
                                user_id
                            ):
                                # Use the store's verify_device method
                                if hasattr(matrix_client.olm.store, "verify_device"):
                                    matrix_client.olm.store.verify_device(device)
                                    logger.debug(
                                        f"Verified device {device.device_id} for user {user_id}"
                                    )

                # Query and claim keys for all devices in the room
                users_devices = {}
                for user_id in room.users:
                    if user_id != matrix_client.user_id:  # Skip our own user
                        devices = matrix_client.device_store.active_user_devices(
                            user_id
                        )
                        if devices:
                            users_devices[user_id] = [
                                device.device_id for device in devices
                            ]

                # Query keys for all devices
                if users_devices:
                    logger.debug(
                        f"Querying keys for users in room {room_id} after error: {list(users_devices.keys())}"
                    )
                    try:
                        await matrix_client.keys_query(list(users_devices.keys()))
                        logger.debug("Keys query completed successfully after error")
                    except Exception as ke:
                        logger.warning(f"Error querying keys after error: {ke}")

                # Claim keys for all devices
                if users_devices:
                    logger.debug(
                        f"Claiming keys for devices in room {room_id} after error"
                    )
                    try:
                        await matrix_client.keys_claim(users_devices)
                        logger.debug("Keys claim completed successfully after error")
                    except Exception as ke:
                        logger.warning(f"Error claiming keys after error: {ke}")

                # Force a new group session
                if matrix_client.olm:
                    await matrix_client.share_group_session(
                        room_id, ignore_unverified_devices=True
                    )
                    logger.debug(
                        f"Shared new group session for room {room_id} after verification"
                    )

                # Retry sending the message with explicit ignore_unverified_devices=True
                response = await asyncio.wait_for(
                    matrix_client.room_send(
                        room_id=room_id,
                        message_type="m.room.message",
                        content=content,
                        ignore_unverified_devices=True,
                    ),
                    timeout=10.0,
                )
                logger.info(
                    f"Successfully sent message after verification bypass to room: {room_id}"
                )
            except Exception as retry_error:
                logger.error(
                    f"Failed to send message even after verification bypass: {retry_error}"
                )
                return
        except Exception as e:
            logger.error(f"Error sending message to Matrix room {room_id}: {e}")
            return

        # Only store message map if relay_reactions is True and meshtastic_id is present and not an emote.
        # If relay_reactions is False, we skip storing entirely.
        if (
            relay_reactions
            and meshtastic_id is not None
            and not emote
            and hasattr(response, "event_id")
        ):
            try:
                # Store the message map
                store_message_map(
                    meshtastic_id,
                    response.event_id,
                    room_id,
                    meshtastic_text if meshtastic_text else message,
                    meshtastic_meshnet=local_meshnet_name,
                )
                logger.debug(f"Stored message map for meshtastic_id: {meshtastic_id}")

                # If msgs_to_keep > 0, prune old messages after inserting a new one
                if msgs_to_keep > 0:
                    prune_message_map(msgs_to_keep)
            except Exception as e:
                logger.error(f"Error storing message map: {e}")

    except asyncio.TimeoutError:
        logger.error("Timed out while waiting for Matrix response")
    except Exception as e:
        logger.error(f"Error sending radio message to matrix room {room_id}: {e}")


def truncate_message(text, max_bytes=227):
    """
    Truncate the given text to fit within the specified byte size.

    :param text: The text to truncate.
    :param max_bytes: The maximum allowed byte size for the truncated text.
    :return: The truncated text.
    """
    truncated_text = text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    return truncated_text


def strip_quoted_lines(text: str) -> str:
    """
    Remove lines that begin with '>' to avoid including
    the original quoted part of a Matrix reply in reaction text.
    """
    lines = text.splitlines()
    filtered = [line for line in lines if not line.strip().startswith(">")]
    return " ".join(filtered).strip()


# Callback for new messages in Matrix room
async def on_room_message(
    room: MatrixRoom,
    event: Union[
        RoomMessageText,
        RoomMessageNotice,
        ReactionEvent,
        RoomMessageEmote,
        MegolmEvent,
        RoomEncryptionEvent,
    ],
) -> None:
    """
    Handle new messages and reactions in Matrix. For reactions, we ensure that when relaying back
    to Meshtastic, we always apply our local meshnet_name to outgoing events.

    We must be careful not to relay reactions to reactions (reaction-chains),
    especially remote reactions that got relayed into the room as m.emote events,
    as we do not store them in the database. If we can't find the original message in the DB,
    it likely means it's a reaction to a reaction, and we stop there.

    Additionally, we only deal with message_map storage (and thus reaction linking)
    if relay_reactions is True. If it's False, none of these mappings are stored or used.
    """
    # Importing here to avoid circular imports and to keep logic consistent
    # Note: We do not call store_message_map directly here for inbound matrix->mesh messages.
    # That logic occurs inside matrix_relay if needed.
    full_display_name = "Unknown user"
    message_timestamp = event.server_timestamp

    # We do not relay messages that occurred before the bot started
    if message_timestamp < bot_start_time:
        return

    # Do not process messages from the bot itself
    if event.sender == bot_user_id:
        return

    # Find the room_config that matches this room, if any
    room_config = None
    for room_conf in matrix_rooms:
        if room_conf["id"] == room.room_id:
            room_config = room_conf
            break

    # Only proceed if the room is supported
    if not room_config:
        return

    relates_to = event.source["content"].get("m.relates_to")
    global config

    # Check if config is available
    if not config:
        logger.error("No configuration available for Matrix message processing.")

    is_reaction = False
    reaction_emoji = None
    original_matrix_event_id = None

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot process Matrix message.")
        return

    # Retrieve relay_reactions option from config, now defaulting to False
    relay_reactions = config["meshtastic"].get("relay_reactions", False)

    # Handle RoomEncryptionEvent - log when a room becomes encrypted
    if isinstance(event, RoomEncryptionEvent):
        logger.info(f"Room {room.room_id} is now encrypted")
        return

    # Handle MegolmEvent (encrypted messages)
    if isinstance(event, MegolmEvent):
        # If the event is encrypted but not yet decrypted, log and return
        if not event.decrypted:
            logger.warning(
                f"Received encrypted event that could not be decrypted in room {room.room_id}"
            )

            # Try to handle the undecryptable event
            try:
                if matrix_client.olm and matrix_client.device_store:
                    sender = event.sender
                    logger.info(
                        f"Attempting to handle undecryptable event from {sender}"
                    )

                    # 1. Verify and trust all devices for this sender
                    for device in matrix_client.device_store.active_user_devices(
                        sender
                    ):
                        if hasattr(matrix_client.olm.store, "verify_device"):
                            matrix_client.olm.store.verify_device(device)
                            logger.debug(
                                f"Verified device {device.device_id} for user {sender}"
                            )
                        if hasattr(matrix_client.olm.store, "mark_device_as_trusted"):
                            matrix_client.olm.store.mark_device_as_trusted(device)
                            logger.debug(
                                f"Trusted device {device.device_id} for user {sender}"
                            )

                    # 2. Upload our keys
                    if matrix_client.should_upload_keys:
                        await matrix_client.keys_upload()
                        logger.debug(f"Uploaded keys for {sender}")

                    # 3. Request keys from the sender
                    try:
                        # Request keys from the sender's devices
                        user_devices = {}
                        user_devices[sender] = [
                            device.device_id
                            for device in matrix_client.device_store.active_user_devices(
                                sender
                            )
                        ]
                        if user_devices[sender]:
                            logger.debug(
                                f"Requesting keys from {sender}'s devices: {user_devices[sender]}"
                            )
                            await matrix_client.keys_claim(user_devices)
                            logger.debug(f"Claimed keys from {sender}")
                    except Exception as key_error:
                        logger.warning(f"Error claiming keys: {key_error}")

                    # 4. Force a sync to get updated keys
                    logger.debug("Forcing sync to get updated keys")
                    await matrix_client.sync(timeout=5000)

                    # 5. Try to decrypt the event again
                    if hasattr(matrix_client, "decrypt_event") and callable(
                        matrix_client.decrypt_event
                    ):
                        try:
                            logger.debug("Attempting to decrypt the event again")
                            decrypted = await matrix_client.decrypt_event(event)
                            if decrypted:
                                logger.info(
                                    "Successfully decrypted event after key claim!"
                                )
                                # Continue processing with the decrypted event
                                return
                        except Exception as decrypt_error:
                            logger.warning(
                                f"Failed to decrypt event after key claim: {decrypt_error}"
                            )
            except Exception as e:
                logger.warning(f"Error trying to handle undecryptable event: {e}")

            # Log a more helpful message
            logger.info(
                "To fix encryption issues, try restarting the relay or clearing the store directory."
            )
            logger.info(f"Current store directory: {get_e2ee_store_dir()}")
            logger.info(
                "You can also try logging out and back in to your Matrix client."
            )

            return

        # For decrypted events, the decrypted content is in event.source["content"]
        # Continue processing as normal
        logger.debug(f"Successfully decrypted message in room {room.room_id}")

    # Check if this is a Matrix ReactionEvent (usually m.reaction)
    if isinstance(event, ReactionEvent):
        # This is a reaction event
        is_reaction = True
        logger.debug(f"Processing Matrix reaction event: {event.source}")
        if relates_to and "event_id" in relates_to and "key" in relates_to:
            # Extract the reaction emoji and the original event it relates to
            reaction_emoji = relates_to["key"]
            original_matrix_event_id = relates_to["event_id"]
            logger.debug(
                f"Original matrix event ID: {original_matrix_event_id}, Reaction emoji: {reaction_emoji}"
            )

    # Check if this is a Matrix RoomMessageEmote (m.emote)
    if isinstance(event, RoomMessageEmote):
        logger.debug(f"Processing Matrix reaction event: {event.source}")
        # For RoomMessageEmote, treat as remote reaction if meshtastic_replyId exists
        is_reaction = True
        # We need to manually extract the reaction emoji from the body
        reaction_body = event.source["content"].get("body", "")
        reaction_match = re.search(r"reacted (.+?) to", reaction_body)
        reaction_emoji = reaction_match.group(1).strip() if reaction_match else "?"

    text = event.body.strip() if (not is_reaction and hasattr(event, "body")) else ""

    longname = event.source["content"].get("meshtastic_longname")
    shortname = event.source["content"].get("meshtastic_shortname", None)
    meshnet_name = event.source["content"].get("meshtastic_meshnet")
    meshtastic_replyId = event.source["content"].get("meshtastic_replyId")
    suppress = event.source["content"].get("mmrelay_suppress")

    # If a message has suppress flag, do not process
    if suppress:
        return

    # If this is a reaction and relay_reactions is False, do nothing
    if is_reaction and not relay_reactions:
        logger.debug(
            "Reaction event encountered but relay_reactions is disabled. Doing nothing."
        )
        return

    local_meshnet_name = config["meshtastic"]["meshnet_name"]

    # If this is a reaction and relay_reactions is True, attempt to relay it
    if is_reaction and relay_reactions:
        # Check if we need to relay a reaction from a remote meshnet to our local meshnet.
        # If meshnet_name != local_meshnet_name and meshtastic_replyId is present and this is an emote,
        # it's a remote reaction that needs to be forwarded as a text message describing the reaction.
        if (
            meshnet_name
            and meshnet_name != local_meshnet_name
            and meshtastic_replyId
            and isinstance(event, RoomMessageEmote)
        ):
            logger.info(f"Relaying reaction from remote meshnet: {meshnet_name}")

            short_meshnet_name = meshnet_name[:4]

            # Format the reaction message for relaying to the local meshnet.
            # The necessary information is in the m.emote event
            if not shortname:
                shortname = longname[:3] if longname else "???"

            meshtastic_text_db = event.source["content"].get("meshtastic_text", "")
            # Strip out any quoted lines from the text
            meshtastic_text_db = strip_quoted_lines(meshtastic_text_db)
            meshtastic_text_db = meshtastic_text_db.replace("\n", " ").replace(
                "\r", " "
            )

            abbreviated_text = (
                meshtastic_text_db[:40] + "..."
                if len(meshtastic_text_db) > 40
                else meshtastic_text_db
            )

            reaction_message = f'{shortname}/{short_meshnet_name} reacted {reaction_emoji} to "{abbreviated_text}"'

            # Relay the remote reaction to the local meshnet.
            meshtastic_interface = connect_meshtastic()
            from mmrelay.meshtastic_utils import logger as meshtastic_logger

            meshtastic_channel = room_config["meshtastic_channel"]

            if config["meshtastic"]["broadcast_enabled"]:
                meshtastic_logger.info(
                    f"Relaying reaction from remote meshnet {meshnet_name} to radio broadcast"
                )
                logger.debug(
                    f"Sending reaction to Meshtastic with meshnet={local_meshnet_name}: {reaction_message}"
                )
                meshtastic_interface.sendText(
                    text=reaction_message, channelIndex=meshtastic_channel
                )
            # We've relayed the remote reaction to our local mesh, so we're done.
            return

        # If original_matrix_event_id is set, this is a reaction to some other matrix event
        if original_matrix_event_id:
            orig = get_message_map_by_matrix_event_id(original_matrix_event_id)
            if not orig:
                # If we don't find the original message in the DB, we suspect it's a reaction-to-reaction scenario
                logger.debug(
                    "Original message for reaction not found in DB. Possibly a reaction-to-reaction scenario. Not forwarding."
                )
                return

            # orig = (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
            meshtastic_id, matrix_room_id, meshtastic_text_db, meshtastic_meshnet_db = (
                orig
            )
            display_name_response = await matrix_client.get_displayname(event.sender)
            full_display_name = display_name_response.displayname or event.sender

            # If not from a remote meshnet, proceed as normal to relay back to the originating meshnet
            short_display_name = full_display_name[:5]
            prefix = f"{short_display_name}[M]: "

            # Remove quoted lines so we don't bring in the original '>' lines from replies
            meshtastic_text_db = strip_quoted_lines(meshtastic_text_db)
            meshtastic_text_db = meshtastic_text_db.replace("\n", " ").replace(
                "\r", " "
            )

            abbreviated_text = (
                meshtastic_text_db[:40] + "..."
                if len(meshtastic_text_db) > 40
                else meshtastic_text_db
            )

            # Always use our local meshnet_name for outgoing events
            reaction_message = (
                f'{prefix}reacted {reaction_emoji} to "{abbreviated_text}"'
            )
            meshtastic_interface = connect_meshtastic()
            from mmrelay.meshtastic_utils import logger as meshtastic_logger

            meshtastic_channel = room_config["meshtastic_channel"]

            if config["meshtastic"]["broadcast_enabled"]:
                meshtastic_logger.info(
                    f"Relaying reaction from {full_display_name} to radio broadcast"
                )
                logger.debug(
                    f"Sending reaction to Meshtastic with meshnet={local_meshnet_name}: {reaction_message}"
                )
                meshtastic_interface.sendText(
                    text=reaction_message, channelIndex=meshtastic_channel
                )
            return

    # For Matrix->Mesh messages from a remote meshnet, rewrite the message format
    if longname and meshnet_name:
        # Always include the meshnet_name in the full display name.
        full_display_name = f"{longname}/{meshnet_name}"

        if meshnet_name != local_meshnet_name:
            # A message from a remote meshnet relayed into Matrix, now going back out
            logger.info(f"Processing message from remote meshnet: {meshnet_name}")
            short_meshnet_name = meshnet_name[:4]
            # If shortname is not available, derive it from the longname
            if shortname is None:
                shortname = longname[:3] if longname else "???"
            # Remove the original prefix "[longname/meshnet]: " to avoid double-tagging
            text = re.sub(rf"^\[{full_display_name}\]: ", "", text)
            text = truncate_message(text)
            full_message = f"{shortname}/{short_meshnet_name}: {text}"
        else:
            # If this message is from our local meshnet (loopback), we ignore it
            return
    else:
        # Normal Matrix message from a Matrix user
        display_name_response = await matrix_client.get_displayname(event.sender)
        full_display_name = display_name_response.displayname or event.sender
        short_display_name = full_display_name[:5]
        prefix = f"{short_display_name}[M]: "
        logger.debug(f"Processing matrix message from [{full_display_name}]: {text}")
        full_message = f"{prefix}{text}"
        text = truncate_message(text)

    # Plugin functionality
    from mmrelay.plugin_loader import load_plugins

    plugins = load_plugins()

    found_matching_plugin = False
    for plugin in plugins:
        if not found_matching_plugin:
            try:
                found_matching_plugin = await plugin.handle_room_message(
                    room, event, full_message
                )
                if found_matching_plugin:
                    logger.info(
                        f"Processed command with plugin: {plugin.plugin_name} from {event.sender}"
                    )
            except Exception as e:
                logger.error(
                    f"Error processing message with plugin {plugin.plugin_name}: {e}"
                )

    # Check if the message is a command directed at the bot
    is_command = False
    for plugin in plugins:
        for command in plugin.get_matrix_commands():
            if bot_command(command, event):
                is_command = True
                break
        if is_command:
            break

    # If this is a command, we do not send it to the mesh
    if is_command:
        logger.debug("Message is a command, not sending to mesh")
        return

    # Connect to Meshtastic
    meshtastic_interface = connect_meshtastic()
    from mmrelay.meshtastic_utils import logger as meshtastic_logger

    if not meshtastic_interface:
        logger.error("Failed to connect to Meshtastic. Cannot relay message.")
        return

    meshtastic_channel = room_config["meshtastic_channel"]

    # If message is from Matrix and broadcast_enabled is True, relay to Meshtastic
    # Note: If relay_reactions is False, we won't store message_map, but we can still relay.
    # The lack of message_map storage just means no reaction bridging will occur.
    if not found_matching_plugin:
        if config["meshtastic"]["broadcast_enabled"]:
            portnum = event.source["content"].get("meshtastic_portnum")
            if portnum == "DETECTION_SENSOR_APP":
                # If detection_sensor is enabled, forward this data as detection sensor data
                if config["meshtastic"].get("detection_sensor", False):
                    sent_packet = meshtastic_interface.sendData(
                        data=full_message.encode("utf-8"),
                        channelIndex=meshtastic_channel,
                        portNum=meshtastic.protobuf.portnums_pb2.PortNum.DETECTION_SENSOR_APP,
                    )
                    # If relay_reactions is True, we store the message map for these messages as well.
                    # If False, skip storing.
                    if relay_reactions and sent_packet and hasattr(sent_packet, "id"):
                        store_message_map(
                            sent_packet.id,
                            event.event_id,
                            room.room_id,
                            text,
                            meshtastic_meshnet=local_meshnet_name,
                        )
                        # Check database config for message map settings (preferred format)
                        database_config = config.get("database", {})
                        msg_map_config = database_config.get("msg_map", {})

                        # If not found in database config, check legacy db config
                        if not msg_map_config:
                            db_config = config.get("db", {})
                            legacy_msg_map_config = db_config.get("msg_map", {})

                            if legacy_msg_map_config:
                                msg_map_config = legacy_msg_map_config
                                logger.warning(
                                    "Using 'db.msg_map' configuration (legacy). 'database.msg_map' is now the preferred format and 'db.msg_map' will be deprecated in a future version."
                                )
                        msgs_to_keep = msg_map_config.get("msgs_to_keep", 500)
                        if msgs_to_keep > 0:
                            prune_message_map(msgs_to_keep)
                else:
                    meshtastic_logger.debug(
                        f"Detection sensor packet received from {full_display_name}, but detection sensor processing is disabled."
                    )
            else:
                meshtastic_logger.info(
                    f"Relaying message from {full_display_name} to radio broadcast"
                )

                try:
                    sent_packet = meshtastic_interface.sendText(
                        text=full_message, channelIndex=meshtastic_channel
                    )
                except Exception as e:
                    meshtastic_logger.error(f"Error sending message to Meshtastic: {e}")
                    return
                # Store message_map only if relay_reactions is True
                if relay_reactions and sent_packet and hasattr(sent_packet, "id"):
                    store_message_map(
                        sent_packet.id,
                        event.event_id,
                        room.room_id,
                        text,
                        meshtastic_meshnet=local_meshnet_name,
                    )
                    # Check database config for message map settings (preferred format)
                    database_config = config.get("database", {})
                    msg_map_config = database_config.get("msg_map", {})

                    # If not found in database config, check legacy db config
                    if not msg_map_config:
                        db_config = config.get("db", {})
                        legacy_msg_map_config = db_config.get("msg_map", {})

                        if legacy_msg_map_config:
                            msg_map_config = legacy_msg_map_config
                            logger.warning(
                                "Using 'db.msg_map' configuration (legacy). 'database.msg_map' is now the preferred format and 'db.msg_map' will be deprecated in a future version."
                            )
                    msgs_to_keep = msg_map_config.get("msgs_to_keep", 500)
                    if msgs_to_keep > 0:
                        prune_message_map(msgs_to_keep)
        else:
            logger.debug(
                f"Broadcast not supported: Message from {full_display_name} dropped."
            )


async def upload_image(
    client: AsyncClient, image: Image.Image, filename: str
) -> UploadResponse:
    """
    Uploads an image to Matrix and returns the UploadResponse containing the content URI.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_data = buffer.getvalue()

    response, maybe_keys = await client.upload(
        io.BytesIO(image_data),
        content_type="image/png",
        filename=filename,
        filesize=len(image_data),
    )

    return response


async def send_room_image(
    client: AsyncClient, room_id: str, upload_response: UploadResponse
):
    """
    Sends an already uploaded image to the specified room.
    """
    await client.room_send(
        room_id=room_id,
        message_type="m.room.message",
        content={"msgtype": "m.image", "url": upload_response.content_uri, "body": ""},
    )


async def login_matrix_bot(
    homeserver=None, username=None, password=None, logout_others=False
):
    """
    Login to Matrix as a bot and save the access token.

    Args:
        homeserver: The Matrix homeserver URL
        username: The Matrix username
        password: The Matrix password
        logout_others: Whether to log out other sessions

    Returns:
        dict: A dictionary with the login information including access_token and device_id
    """
    import getpass

    import yaml

    from mmrelay.config import get_config_paths

    # Get homeserver URL
    if not homeserver:
        homeserver = input("Enter Matrix homeserver URL (e.g., https://matrix.org): ")

    # Ensure homeserver URL has the correct format
    if not (homeserver.startswith("https://") or homeserver.startswith("http://")):
        # AsyncClient expects the URL with the protocol
        homeserver = "https://" + homeserver

    # Get username
    if not username:
        username = input("Enter Matrix username (without @): ")

    # Format username exactly like m2m-lite does
    username = f"@{username}" if not username.startswith("@") else username
    server_name = urlparse(homeserver).netloc
    username = f"{username}:{server_name}" if ":" not in username else username
    logger.debug(f"Using username: {username}")

    # Get password
    if not password:
        import getpass

        password = getpass.getpass("Enter Matrix password: ")

    # Ask about logging out other sessions
    if logout_others is None:
        logout_others_input = input("Log out other sessions? (Y/n) [Default: Yes]: ").lower()
        # Default to True if empty or starts with 'y'
        logout_others = not logout_others_input.startswith("n") if logout_others_input else True

    # Create a Matrix client for login
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    client_config = AsyncClientConfig(store_sync_tokens=True, encryption_enabled=True)

    # Check if we have existing credentials to reuse the device_id
    existing_device_id = None
    try:
        from mmrelay.config import get_base_dir, get_e2ee_store_dir
        config_dir = get_base_dir()
        credentials_path = os.path.join(config_dir, "credentials.json")

        if os.path.exists(credentials_path):
            with open(credentials_path, "r") as f:
                existing_creds = json.load(f)
                if "device_id" in existing_creds and existing_creds["user_id"] == username:
                    existing_device_id = existing_creds["device_id"]
                    logger.info(f"Reusing existing device_id: {existing_device_id}")
    except Exception as e:
        logger.debug(f"Could not load existing credentials: {e}")

    # Get the E2EE store path
    store_path = get_e2ee_store_dir()
    os.makedirs(store_path, exist_ok=True)
    logger.info(f"Using E2EE store path: {store_path}")

    # Check if store directory contains database files
    store_files = os.listdir(store_path) if os.path.exists(store_path) else []
    db_files = [f for f in store_files if f.endswith('.db')]
    if db_files:
        logger.info(f"Found existing E2EE store files: {', '.join(db_files)}")
    else:
        logger.warning("No existing E2EE store files found. Encryption may not work correctly.")

    # Initialize client with existing device_id if available and store_path for E2EE
    client = AsyncClient(homeserver, username, device_id=existing_device_id, config=client_config, ssl=ssl_context, store_path=store_path)

    # Login
    logger.info(f"Logging in as {username} to {homeserver}...")
    # Login with the existing device_id if available, otherwise let the server assign one
    try:
        # Use a consistent device name to help with identification
        device_name = "mmrelay-e2ee"

        # If we have an existing device_id, use it to maintain encryption keys
        if existing_device_id:
            response = await client.login(password, device_name=device_name, device_id=existing_device_id)
        else:
            # Let the server assign a new device_id but use our consistent device name
            response = await client.login(password, device_name=device_name)
    except Exception as e:
        logger.error(f"Error during login: {e}")
        await client.close()
        return None

    if hasattr(response, "access_token") and response.access_token:
        logger.info("Login successful!")

        # Get user ID
        user_id = response.user_id
        device_id = response.device_id
        access_token = response.access_token

        # Save credentials to credentials.json
        credentials = {
            "user_id": user_id,
            "device_id": device_id,
            "access_token": access_token,
            "homeserver": homeserver
        }

        # Get the config directory
        from mmrelay.config import get_base_dir
        config_dir = get_base_dir()
        os.makedirs(config_dir, exist_ok=True)

        # Save credentials to file
        credentials_path = os.path.join(config_dir, "credentials.json")
        try:
            with open(credentials_path, "w") as f:
                import json
                json.dump(credentials, f)
            logger.info(f"Credentials saved to {credentials_path}")
        except Exception as e:
            logger.warning(f"Failed to save credentials: {e}")

        # Original homeserver URL with protocol for config
        original_homeserver = homeserver

        # Use the same config directory for config.yaml
        # config_dir is already set from above
        os.makedirs(config_dir, exist_ok=True)

        # Save credentials to credentials.json
        credentials = {
            "user_id": user_id,
            "device_id": device_id,
            "access_token": access_token,
            "homeserver": homeserver
        }

        # Save credentials to file
        credentials_path = os.path.join(config_dir, "credentials.json")
        try:
            with open(credentials_path, "w") as f:
                import json
                json.dump(credentials, f)
            logger.info(f"Credentials saved to {credentials_path}")
            logger.info("NOTE: Using credentials.json for login instead of config.yaml")
            logger.info("You can safely remove Matrix login details from config.yaml if desired")
        except Exception as e:
            logger.warning(f"Failed to save credentials: {e}")

        # Log out other sessions if requested
        if logout_others:
            logger.info("Logging out other sessions...")
            # Note: The matrix-nio library doesn't provide a direct way to log out other devices
            # We'll skip this functionality for now
            logger.info("Skipping logout of other sessions - not supported in current matrix-nio version")
            logger.info("Device logout process completed")

        # Close the client
        await client.close()
    else:
        error_msg = getattr(response, "message", "Unknown error")
        logger.error(f"Login failed: {error_msg}")
        await client.close()
        return None

        # Get config file path
        config_paths = get_config_paths()
        config_file = config_paths[0]  # Use the first config path

        # Load existing config if it exists
        config = {}
        if os.path.exists(config_file):
            with open(config_file, "r") as f:
                config = yaml.load(f, Loader=yaml.SafeLoader) or {}

        # Update config with new login info
        if "matrix" not in config:
            config["matrix"] = {}

        config["matrix"]["homeserver"] = original_homeserver
        config["matrix"]["access_token"] = access_token
        config["matrix"]["bot_user_id"] = user_id

        # Add E2EE config if not present
        if "e2ee" not in config["matrix"]:
            config["matrix"]["e2ee"] = {"enabled": True}

        # Save config
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        with open(config_file, "w") as f:
            yaml.dump(config, f, default_flow_style=False)

        logger.info(f"Login information saved to {config_file}")

        # Close the client
        await client.close()

        return {
            "homeserver": homeserver,
            "user_id": user_id,
            "device_id": device_id,
            "access_token": access_token,
        }
