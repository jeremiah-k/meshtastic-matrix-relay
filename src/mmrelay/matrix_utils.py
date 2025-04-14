import asyncio
import io
import json
import os
import re
import ssl
import time
from typing import Dict, Union
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
    ToDeviceMessage,
    UnknownToDeviceEvent,
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


async def verify_own_device(matrix_client: AsyncClient) -> bool:
    """
    Manually verify the bot's own device to remove the red shield warning in Element.

    This function verifies the current device in the device store, which helps
    eliminate the "unverified session" warning in Element and other Matrix clients.

    The key insight is that we need to explicitly force a keys query for our own user ID,
    which matrix-nio normally skips. This ensures our own device is in the device store.

    Args:
        matrix_client: The Matrix client instance

    Returns:
        bool: True if verification was successful, False otherwise
    """
    if not matrix_client.olm or not matrix_client.device_id:
        logger.debug("Cannot verify own device: E2EE not enabled or device_id not set")
        return False

    try:
        # First, ensure we have our own device keys by querying them from the server
        logger.debug(f"Querying device keys for our user {matrix_client.user_id}")
        try:
            # Force a keys query specifically for our own user ID
            # The key is to explicitly pass our user_id in the users parameter
            # This bypasses matrix-nio's normal optimization that skips querying for our own user
            query_response = await matrix_client.keys_query(user_ids=[matrix_client.user_id])

            if (
                hasattr(query_response, "device_keys")
                and matrix_client.user_id in query_response.device_keys
            ):
                logger.debug(
                    f"Received keys for {len(query_response.device_keys[matrix_client.user_id])} devices"
                )
            else:
                logger.warning("Failed to query device keys from server")
        except Exception as e:
            logger.warning(f"Error querying device keys: {e}")

        # Get our own device from the device store (should be populated after keys_query)
        device = None
        for d in matrix_client.device_store.active_user_devices(matrix_client.user_id):
            if d.device_id == matrix_client.device_id:
                device = d
                logger.debug(f"Found our device {d.device_id} in the device store")
                break

        if not device:
            logger.warning(f"Could not find our own device {matrix_client.device_id} in the device store")
            # Log all devices we have for debugging
            devices = list(matrix_client.device_store.active_user_devices(matrix_client.user_id))
            if devices:
                logger.debug(f"Found {len(devices)} devices for our user, but not our device ID")
                for d in devices:
                    logger.debug(f"Device in store: {d.device_id}")
            return False

        # Verify our own device using the client's verify_device method
        matrix_client.verify_device(device)
        logger.info(f"Successfully verified our own device: {matrix_client.device_id}")

        # Also mark the device as trusted if the client has a trust_manager
        trust_manager = getattr(matrix_client.crypto, "trust_manager", None)
        if trust_manager:
            trust_manager.mark_as_trusted(device)
            logger.debug(f"Marked our device {matrix_client.device_id} as trusted via trust_manager")
        elif hasattr(matrix_client.olm.store, "mark_device_as_trusted"):
            matrix_client.olm.store.mark_device_as_trusted(device)
            logger.debug(f"Marked our device {matrix_client.device_id} as trusted in the store")

        return True
    except Exception as e:
        logger.error(f"Error verifying own device: {e}")
        return False


def is_encryption_enabled(config: Dict) -> bool:
    """
    Check if encryption is enabled in the configuration.
    Checks both 'encryption' and 'e2ee' keys for backward compatibility.

    Args:
        config: The application configuration

    Returns:
        bool: True if encryption is enabled, False otherwise
    """
    if not config or "matrix" not in config:
        return False

    # Check for both encryption and e2ee for backward compatibility
    return (
        ("encryption" in config["matrix"] and config["matrix"]["encryption"].get("enabled", False))
        or ("e2ee" in config["matrix"] and config["matrix"]["e2ee"].get("enabled", False))
    )


async def self_verify_device(matrix_client: AsyncClient) -> bool:
    """
    Implement self-verification of the bot's device using the new verification flow.

    This function sends a verification request to itself and then completes the verification
    process by sending the necessary to-device messages. This is based on the approach from
    the matrix-nio patch that handles the new verification flow.

    Args:
        matrix_client: The Matrix client instance

    Returns:
        bool: True if verification was successful, False otherwise
    """
    if not matrix_client.olm or not matrix_client.device_id or not matrix_client.user_id:
        logger.debug("Cannot self-verify: E2EE not enabled or device_id/user_id not set")
        return False

    try:
        # Generate a transaction ID for this verification
        import uuid
        txid = str(uuid.uuid4())
        logger.debug(f"Starting self-verification with transaction ID: {txid}")

        # Step 1: Send a verification request to ourselves
        request_content = {
            "from_device": matrix_client.device_id,
            "methods": ["m.sas.v1"],  # SAS verification
            "timestamp": int(time.time() * 1000),
            "transaction_id": txid
        }

        request_message = ToDeviceMessage(
            type="m.key.verification.request",
            recipient=matrix_client.user_id,
            recipient_device=matrix_client.device_id,
            content=request_content
        )

        logger.debug("Sending verification request to ourselves")
        resp = await matrix_client.to_device(request_message, txid)
        if isinstance(resp, exceptions.ToDeviceError):
            logger.warning(f"Failed to send verification request: {resp}")
            return False

        # Step 2: Send a ready message to accept the verification
        ready_content = {
            "from_device": matrix_client.device_id,
            "methods": ["m.sas.v1"],
            "transaction_id": txid
        }

        ready_message = ToDeviceMessage(
            type="m.key.verification.ready",
            recipient=matrix_client.user_id,
            recipient_device=matrix_client.device_id,
            content=ready_content
        )

        logger.debug("Sending verification ready message")
        resp = await matrix_client.to_device(ready_message, txid)
        if isinstance(resp, exceptions.ToDeviceError):
            logger.warning(f"Failed to send verification ready message: {resp}")
            return False

        # Step 3: Send a done message to complete the verification
        done_content = {
            "transaction_id": txid
        }

        done_message = ToDeviceMessage(
            type="m.key.verification.done",
            recipient=matrix_client.user_id,
            recipient_device=matrix_client.device_id,
            content=done_content
        )

        logger.debug("Sending verification done message")
        resp = await matrix_client.to_device(done_message, txid)
        if isinstance(resp, exceptions.ToDeviceError):
            logger.warning(f"Failed to send verification done message: {resp}")
            return False

        logger.info("Self-verification process completed successfully")
        return True
    except Exception as e:
        logger.error(f"Error during self-verification: {e}")
        return False

async def initialize_e2ee(matrix_client: AsyncClient, config: Dict) -> None:
    """
    Initialize end-to-end encryption for Matrix client after initial sync.
    This function handles key uploading, device verification, and group session sharing.

    Args:
        matrix_client: The Matrix client instance
        config: The application configuration
    """
    # Check if encryption is enabled in config and if olm is available
    if not is_encryption_enabled(config) or not matrix_client.olm:
        return

    logger.info("Initializing end-to-end encryption...")

    # 1. Explicitly upload encryption keys and wait for completion
    logger.debug("Explicitly uploading encryption keys...")
    try:
        # Use the direct olm method to ensure we wait for completion
        await matrix_client.olm.upload_keys()
        logger.debug("Encryption keys uploaded successfully")
    except Exception as ke:
        logger.debug(f"Key upload info: {ke}")

    # 2. Perform a longer sync to ensure device keys are properly populated
    logger.debug("Performing sync to ensure device keys are properly populated...")
    await matrix_client.sync(timeout=10000)  # 10 second timeout

    # 3. Verify our own device to remove the red shield warning in Element
    logger.debug("Verifying our own device...")
    verified = await verify_own_device(matrix_client)
    if verified:
        logger.info(
            "Successfully verified our own device - this should remove the red shield warning in Element"
        )
    else:
        # If first attempt failed, try again after another sync
        logger.debug(
            "First verification attempt failed, trying again after another sync..."
        )
        await matrix_client.sync(timeout=5000)  # 5 second timeout
        verified = await verify_own_device(matrix_client)
        if verified:
            logger.info("Successfully verified our own device on second attempt")
        else:
            logger.warning(
                "Could not verify our own device - you may still see a red shield warning in Element"
            )

        # If traditional verification failed, try the new self-verification approach
        logger.debug("Attempting self-verification using the new verification flow...")
        self_verified = await self_verify_device(matrix_client)
        if self_verified:
            logger.info("Successfully self-verified our device using the new verification flow")
        else:
            logger.warning("Self-verification also failed - you may still see a red shield warning in Element")

    # 4. Trust all devices for all users in our rooms
    # This is important to avoid verification errors when sending messages
    logger.debug("Trusting all devices for all users in our rooms...")
    try:
        # Get all users in our rooms
        all_users = set()
        for _, room in matrix_client.rooms.items():
            all_users.update(room.users)

        # Trust all devices for each user
        trusted_count = 0
        for user_id in all_users:
            # Skip our own user as we already handled it
            if user_id == matrix_client.user_id:
                continue

            # Trust all devices for this user
            try:
                # Get all devices for this user
                user_devices = list(matrix_client.device_store.active_user_devices(user_id))
                for device in user_devices:
                    matrix_client.verify_device(device)
                    trusted_count += 1

                    # Mark the device as trusted in the store if the method exists
                    if hasattr(matrix_client.olm.store, "mark_device_as_trusted"):
                        matrix_client.olm.store.mark_device_as_trusted(device)
            except Exception as e:
                logger.debug(f"Error trusting devices for user {user_id}: {e}")

        if trusted_count > 0:
            logger.debug(f"Trusted {trusted_count} devices for other users")
    except Exception as e:
        logger.warning(f"Error trusting devices for other users: {e}")

    # 5. Share group sessions for all encrypted rooms
    encrypted_rooms = [
        room_id for room_id, room in matrix_client.rooms.items() if room.encrypted
    ]
    if encrypted_rooms:
        logger.info(
            f"Sharing group sessions for {len(encrypted_rooms)} encrypted rooms"
        )
        for room_id in encrypted_rooms:
            try:
                # First ensure we have all the members of the room
                try:
                    members_response = await matrix_client.joined_members(room_id)
                    if hasattr(members_response, "members"):
                        logger.debug(f"Retrieved {len(members_response.members)} members for room {room_id}")
                    else:
                        logger.debug(f"Retrieved members for room {room_id}")
                except Exception as me:
                    logger.warning(f"Error retrieving members for room {room_id}: {me}")

                # Make sure the room is actually encrypted
                room = matrix_client.rooms.get(room_id)
                if not room or not room.encrypted:
                    logger.warning(f"Room {room_id} is not marked as encrypted, skipping group session")
                    continue

                # Then share the group session with ignore_unverified_devices=True
                logger.debug(f"Sharing group session for room {room_id}")
                response = await matrix_client.share_group_session(room_id, ignore_unverified_devices=True)

                if hasattr(response, "room_id"):
                    logger.info(f"Successfully shared group session for room {room_id}")
                else:
                    logger.info(f"Shared group session for room {room_id}")

                # Verify that the session was shared successfully
                if matrix_client.olm and hasattr(matrix_client.olm, "outbound_group_sessions"):
                    if room_id in matrix_client.olm.outbound_group_sessions:
                        logger.info(f"Confirmed outbound group session exists for room {room_id}")
                    else:
                        logger.warning(f"No outbound group session found for room {room_id} after sharing")
            except Exception as e:
                logger.warning(f"Could not share group session for room {room_id}: {e}")
    else:
        logger.warning("No encrypted rooms found - encryption may not be working properly")

    logger.info("End-to-end encryption initialization complete")


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
            logger.info(f"Using Matrix credentials from {credentials_path}")
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
        # If config also has Matrix login info, let the user know we're ignoring it
        if config and "matrix" in config and "access_token" in config["matrix"]:
            logger.debug("Ignoring Matrix login details in config.yaml in favor of credentials.json")
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

    # Check if encryption is enabled
    e2ee_enabled = False
    e2ee_store_path = None
    e2ee_device_id = None

    try:
        # Check for both encryption and e2ee for backward compatibility
        if (
            "encryption" in config["matrix"]
            and config["matrix"]["encryption"].get("enabled", False)
        ) or (
            "e2ee" in config["matrix"]
            and config["matrix"]["e2ee"].get("enabled", False)
        ):
            # Prioritize encryption over e2ee if both are present
            encryption_config = config["matrix"].get(
                "encryption", config["matrix"].get("e2ee", {})
            )
            # Check if python-olm is installed
            try:
                import olm  # noqa: F401

                e2ee_enabled = True
                logger.info("End-to-End Encryption (E2EE) is enabled")

                # Get store path from config or use default
                if "store_path" in encryption_config:
                    e2ee_store_path = os.path.expanduser(
                        encryption_config["store_path"]
                    )
                else:
                    from mmrelay.config import get_e2ee_store_dir

                    e2ee_store_path = get_e2ee_store_dir()

                # Create store directory if it doesn't exist
                os.makedirs(e2ee_store_path, exist_ok=True)
                logger.debug(f"Using E2EE store path: {e2ee_store_path}")

                # We'll get the device ID from whoami() later
                e2ee_device_id = None
                logger.debug("Will retrieve device_id from whoami() response")
            except ImportError:
                logger.warning(
                    "Encryption is enabled in config but python-olm is not installed."
                )
                logger.warning("Install mmrelay[e2e] to use encryption features.")
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

    # Retrieve the device_id using whoami() - this is critical for E2EE
    whoami_response = await matrix_client.whoami()
    if isinstance(whoami_response, WhoamiError):
        logger.error(f"Failed to retrieve device_id: {whoami_response.message}")
        if e2ee_enabled:
            logger.error(
                "E2EE requires a valid device_id. E2EE may not work correctly."
            )
        matrix_client.device_id = None
    else:
        # Always use the device_id from whoami for consistency
        matrix_client.device_id = whoami_response.device_id
        if matrix_client.device_id:
            logger.info(f"Using device_id from server: {matrix_client.device_id}")
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

    # If E2EE is enabled, load the store and set up encryption
    if e2ee_enabled:
        try:
            # Load the store first
            matrix_client.load_store()

            # Upload encryption keys if needed
            if matrix_client.should_upload_keys:
                logger.debug("Uploading encryption keys to server")
                await matrix_client.keys_upload()

            # Register a callback for UnknownToDeviceEvent to handle verification requests
            # This is needed because matrix-nio doesn't have proper event types for the new verification flow
            async def handle_unknown_to_device(event):
                if hasattr(event, "source") and "type" in event.source:
                    event_type = event.source["type"]
                    if event_type == "m.key.verification.request":
                        logger.info(f"Received verification request from {event.sender}")
                        logger.debug(f"Verification request content: {event.source['content']}")
                        # We don't need to respond to these requests as we're not implementing interactive verification
                        # Just log them for now
                    elif event_type == "m.key.verification.ready":
                        logger.info(f"Received verification ready from {event.sender}")
                    elif event_type == "m.key.verification.done":
                        logger.info(f"Received verification done from {event.sender}")

            # Add the callback for UnknownToDeviceEvent
            matrix_client.add_to_device_callback(handle_unknown_to_device, (UnknownToDeviceEvent,))
            logger.debug("Added callback for UnknownToDeviceEvent to handle verification requests")

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
                        f"Ensuring our own device {matrix_client.device_id} is verified and trusted"
                    )
                    try:
                        # Get our own devices
                        own_devices = matrix_client.device_store.active_user_devices(
                            matrix_client.user_id
                        )
                        for device in own_devices:
                            # Verify and trust all our devices, especially our current one
                            matrix_client.olm.store.verify_device(device)
                            if hasattr(
                                matrix_client.olm.store, "mark_device_as_trusted"
                            ):
                                matrix_client.olm.store.mark_device_as_trusted(device)
                            logger.debug(
                                f"Verified and trusted our device: {device.device_id}"
                            )
                            verified_count += 1

                            # If this is our current device, log it
                            if device.device_id == matrix_client.device_id:
                                logger.info(
                                    f"Verified and trusted our current device: {device.device_id}"
                                )
                    except Exception as e:
                        logger.warning(f"Error verifying our own devices: {e}")

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
                # Force a sync to update the client's rooms list
                logger.debug(f"Forcing sync after joining room {room_id}")
                await matrix_client.sync(timeout=5000)  # Increased timeout for better reliability

                # Check if the room is encrypted
                is_encrypted = False
                if room_id in matrix_client.rooms:
                    room = matrix_client.rooms[room_id]
                    is_encrypted = room.encrypted
                    if is_encrypted:
                        logger.info(f"Joined room '{room_id_or_alias}' successfully (encrypted)")
                    else:
                        logger.info(f"Joined room '{room_id_or_alias}' successfully (not encrypted)")
                else:
                    logger.info(f"Joined room '{room_id_or_alias}' successfully")

                # We already did a sync above, no need for another one

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


async def ensure_encryption_ready(room_id: str) -> bool:
    """
    Ensures that encryption is properly set up for a room before sending a message.
    This helps prevent the first message decryption failure issue.

    Args:
        room_id: The ID of the room to check

    Returns:
        bool: True if encryption is ready, False otherwise
    """
    global matrix_client

    if not matrix_client or not matrix_client.olm:
        return True  # Not using encryption, so we're "ready"

    try:
        # Check if the room is encrypted
        room = matrix_client.rooms.get(room_id)
        if not room or not room.encrypted:
            return True  # Room is not encrypted, so we're "ready"

        # Check if we need to share a group session
        if matrix_client.olm.should_share_group_session(room_id):
            logger.debug(f"Need to share group session for room {room_id} before sending message")

            # First ensure we have all the members of the room
            try:
                await matrix_client.joined_members(room_id)
                logger.debug(f"Retrieved members for room {room_id}")
            except Exception as me:
                logger.warning(f"Error retrieving members for room {room_id}: {me}")

            # Then share the group session
            await matrix_client.share_group_session(room_id, ignore_unverified_devices=True)
            logger.debug(f"Shared group session for room {room_id} before sending message")

            # Verify that the session was shared successfully
            if hasattr(matrix_client.olm, "outbound_group_sessions"):
                if room_id in matrix_client.olm.outbound_group_sessions:
                    logger.debug(f"Confirmed outbound group session exists for room {room_id}")
                    return True
                else:
                    logger.warning(f"No outbound group session found for room {room_id} after sharing")
                    return False
        else:
            # Group session already exists
            logger.debug(f"Group session already exists for room {room_id}")
            return True

        return True
    except Exception as e:
        logger.warning(f"Error ensuring encryption is ready for room {room_id}: {e}")
        return False

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

            if is_encrypted:
                logger.debug(f"Room {room_id} is encrypted, sending with encryption")

                # Make sure we have a group session for this room
                try:
                    # Ensure we have shared a group session
                    if matrix_client.olm:
                        # First, make sure we have keys for all devices in the room
                        users_devices = {}
                        for user_id in room.users:
                            if user_id != matrix_client.user_id:  # Skip our own user
                                # Get all devices for this user
                                devices = (
                                    matrix_client.device_store.active_user_devices(
                                        user_id
                                    )
                                )
                                if devices:
                                    users_devices[user_id] = [
                                        device.device_id for device in devices
                                    ]

                        # Make sure we have keys uploaded
                        if matrix_client.should_upload_keys:
                            logger.debug(
                                "Uploading encryption keys before sending message"
                            )
                            try:
                                await matrix_client.keys_upload()
                                logger.debug("Keys uploaded successfully")
                            except Exception as ke:
                                logger.warning(f"Error uploading keys: {ke}")

                        # Claim keys for all devices
                        if users_devices:
                            logger.debug(f"Claiming keys for devices in room {room_id}")
                            try:
                                await matrix_client.keys_claim(users_devices)
                                logger.debug("Keys claim completed successfully")
                            except Exception as ke:
                                logger.warning(f"Error claiming keys: {ke}")

                        # Force sharing a new group session to ensure all devices get keys
                        logger.debug(f"Sharing new group session for room {room_id}")
                        await matrix_client.share_group_session(
                            room_id, ignore_unverified_devices=True
                        )
                        logger.debug(f"Shared new group session for room {room_id}")
                except Exception as e:
                    logger.warning(f"Error sharing group session: {e}")
            else:
                logger.debug(f"Room {room_id} is not encrypted")

            # Ensure encryption is ready before sending the message
            if matrix_client.olm:
                await ensure_encryption_ready(room_id)

            # Send the message with a timeout
            response = await asyncio.wait_for(
                matrix_client.room_send(
                    room_id=room_id,
                    message_type="m.room.message",
                    content=content,
                    ignore_unverified_devices=True,  # Important: ignore unverified devices
                ),
                timeout=10.0,  # Increased timeout
            )

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
        logout_others_input = input("Log out other sessions? (y/n): ").lower()
        logout_others = logout_others_input.startswith("y")

    # Create a Matrix client for login
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    client_config = AsyncClientConfig(store_sync_tokens=True)
    # Initialize client exactly like m2m-lite does
    client = AsyncClient(
        homeserver, username, device_id="mmrelay", config=client_config, ssl=ssl_context
    )

    # Login
    logger.info(f"Logging in as {username} to {homeserver}...")
    # Login exactly like m2m-lite does
    try:
        response = await client.login(password)
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
            "homeserver": homeserver,
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
            "homeserver": homeserver,
        }

        # Save credentials to file
        credentials_path = os.path.join(config_dir, "credentials.json")
        try:
            with open(credentials_path, "w") as f:
                import json

                json.dump(credentials, f)
            logger.info(f"Credentials saved to {credentials_path}")
            logger.info("NOTE: Using credentials.json for login instead of config.yaml")
            logger.info(
                "You can safely remove Matrix login details from config.yaml if desired"
            )
        except Exception as e:
            logger.warning(f"Failed to save credentials: {e}")

        # Close the client
        await client.close()
    else:
        error_msg = getattr(response, "message", "Unknown error")
        logger.error(f"Login failed: {error_msg}")
        await client.close()
        return None

        # Log out other sessions if requested
        if logout_others:
            logger.info("Logging out other sessions...")
            # Set the access token for the client
            client.access_token = access_token
            client.user_id = user_id

            # Get list of devices
            devices_response = await client.devices()
            if hasattr(devices_response, "devices"):
                for device in devices_response.devices:
                    # Skip the current device
                    if device.device_id == device_id:
                        continue

                    # Log out the device
                    logger.debug(f"Logging out device {device.device_id}")
                    await client.logout_device(device.device_id)
                logger.info("Other sessions logged out successfully")
            else:
                logger.warning(
                    f"Failed to get devices: {devices_response.message if hasattr(devices_response, 'message') else 'Unknown error'}"
                )

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
