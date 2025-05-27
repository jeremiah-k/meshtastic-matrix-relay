# src/mmrelay/matrix/e2ee.py
import asyncio
import logging
import os

from nio import (
    AsyncClient,
    MegolmEvent,
    RoomMessageText, 
    UploadResponse,
    WhoamiError,
    exceptions,
    MatrixRoom, 
)

from mmrelay.log_utils import get_logger 
from mmrelay.config import get_e2ee_store_dir # Added for handle_decryption_failure

logger = get_logger(name="E2EE")

async def initialize_e2ee(client: AsyncClient, config: dict):
    # Logic to be moved from main.py and matrix_utils.connect_matrix
    logger.info("Initializing end-to-end encryption...")

    # 1. Make sure the store is loaded
    logger.debug("Loading encryption store...")
    try:
        # Explicitly load the store
        client.load_store()
        logger.debug("Encryption store loaded successfully")

        # Debug store state
        logger.debug(f"Device store users immediately after load: {list(client.device_store.users) if client.device_store else 'None'}")
    except Exception as le:
        logger.warning(f"Error loading encryption store: {le}")

    # Confirm client credentials are set
    logger.debug(f"Checking client credentials: user_id={client.user_id}, device_id={client.device_id}")
    if not (client.user_id and client.device_id and client.access_token):
        logger.warning("Missing essential credentials for E2EE. Encryption may not work correctly.")

    # 1.5 Upload keys BEFORE first sync
    logger.debug("Uploading encryption keys to server BEFORE sync")
    try:
        if client.should_upload_keys:
            await client.keys_upload()
            logger.debug("Encryption keys uploaded successfully")
        else:
            logger.debug("No key upload needed at this stage")
    except Exception as ke:
        logger.warning(f"Error uploading keys: {ke}")

    # 1.6 Perform sync AFTER key upload
    logger.debug("Performing sync AFTER key upload")
    await client.sync(timeout=5000)
    logger.debug(f"Device store users after sync: {list(client.device_store.users) if client.device_store else 'None'}")

    # Verify that rooms are properly populated
    if not client.rooms:
        logger.warning("No rooms found after sync. Message delivery may not work correctly.")
    else:
        logger.debug(f"Found {len(client.rooms)} rooms after sync")

    # 2. Trust all of our own devices to ensure encryption works
    logger.debug("Trusting our own devices for encryption...")
    try:
        # First make sure we have synced to populate the device store
        logger.debug("Performing sync to populate device store...")
        await client.sync(timeout=5000)

        # Check if our user_id is in the device_store
        if client.device_store and client.user_id in client.device_store:
            devices = client.device_store[client.user_id]
            logger.info(f"Found {len(devices)} of our own devices in the device store")

            # For bots, the pragmatic approach is to mark our own device as ignored
            # This tells matrix-nio to ignore verification status and proceed with encryption
            for device_id, device in devices.items():
                if device_id == client.device_id:
                    # Mark our own device as ignored to avoid verification warnings
                    client.ignore_device(device)
                    logger.debug(f"Marked our own device {device_id} as ignored to avoid verification warnings")

            # Log about our current device
            if client.device_id in devices:
                logger.info(f"Our current device is in the device store: {client.device_id}")
            else:
                logger.debug(f"Our current device {client.device_id} not found in device store (this is normal)")
        else:
            logger.debug("No devices found for our user in the device store (this is normal for first run)")

        # We don't verify other users' devices - we use ignore_unverified_devices instead
        logger.debug("Using ignore_unverified_devices for all rooms")
    except Exception as ve:
        logger.debug(f"Device trust setup info: {ve}")

    # 2. Check if keys need to be uploaded and upload them if needed
    logger.debug("Checking if encryption keys need to be uploaded...")
    logger.debug(f"should_upload_keys = {client.should_upload_keys}")

    # Always try to upload keys to ensure they're properly registered
    logger.debug("Uploading encryption keys...")
    try:
        await client.keys_upload()
        logger.debug("Encryption keys uploaded successfully")
    except Exception as ke:
        if "No key upload needed" in str(ke):
            logger.debug("No key upload needed")
        else:
            logger.warning(f"Error uploading keys: {ke}")

    # 3. Perform another sync to ensure everything is up-to-date
    logger.debug("Performing sync to update encryption state...")
    await client.sync(timeout=10000)  # 10 second timeout

    # 4. Share group sessions for all encrypted rooms
    encrypted_rooms = [room_id for room_id, room in client.rooms.items() if room.encrypted]
    if encrypted_rooms:
        logger.debug(f"Sharing group sessions for {len(encrypted_rooms)} encrypted rooms")
        for room_id in encrypted_rooms:
            try:
                # Use ignore_unverified_devices=True to ensure messages can be sent
                await client.share_group_session(room_id, ignore_unverified_devices=True)
                logger.debug(f"Shared group session for room {room_id}")
            except Exception as e:
                logger.warning(f"Could not share group session for room {room_id}: {e}")
    else:
        logger.debug("No encrypted rooms found")

    # 5. Perform a final sync to ensure all group sessions are properly registered
    logger.debug("Performing final sync to update encryption state...")
    await client.sync(timeout=5000)  # 5 second timeout

    # Log encryption status of all rooms after E2EE setup
    logger.info("End-to-end encryption initialization complete")
    if client.rooms:
        logger.info("Room encryption status after E2EE setup:")
        encrypted_count = 0
        for i, (room_id, room) in enumerate(client.rooms.items(), 1):
            room_name = room.display_name if hasattr(room, "display_name") else "Unknown"
            is_encrypted = room.encrypted if hasattr(room, "encrypted") else False
            if is_encrypted:
                encrypted_count += 1
            encryption_status = "ENCRYPTED" if is_encrypted else "unencrypted"
            logger.info(f"  {i}. {room_name}: {room_id} - {encryption_status}")
        logger.info(f"Total rooms: {len(client.rooms)}, Encrypted: {encrypted_count}")


async def encrypt_content_for_room(client: AsyncClient, room_id: str, content: dict, message_type: str = "m.room.message"):
    logger.debug(f"Room {room_id} is encrypted, sending with encryption")

    # Make sure we have a group session for this room
    try:
        # Ensure we have shared a group session
        if client.olm:
            # Mark our own device as ignored to avoid verification warnings
            logger.debug("Setting up device trust before sending encrypted message...")
            try:
                # Check if our user_id is in the device_store
                if client.device_store and client.user_id in client.device_store:
                    devices = client.device_store[client.user_id]

                    # For bots, the pragmatic approach is to mark our own device as ignored
                    # This tells matrix-nio to ignore verification status and proceed with encryption
                    for device_id, device in devices.items():
                        if device_id == client.device_id:
                            # Mark our own device as ignored to avoid verification warnings
                            client.ignore_device(device)
                            logger.debug(f"Marked our own device {device_id} as ignored to avoid verification warnings")
                else:
                    logger.debug("No devices found for our user in the device store (this is normal for first run)")
            except Exception as ve:
                logger.debug(f"Error setting up device trust: {ve}")

            # We don't verify other users' devices - we use ignore_unverified_devices instead
            logger.debug(f"Using ignore_unverified_devices for room {room_id}")

            # Make sure the store is loaded
            try:
                client.load_store()
                logger.debug("Encryption store loaded successfully")
            except Exception as le:
                logger.warning(f"Error loading encryption store: {le}")

            # Debug device store state
            logger.debug(f"Device store users before key operations: {list(client.device_store.users) if client.device_store else 'None'}")

            # Upload keys BEFORE sync
            logger.debug("Uploading encryption keys BEFORE sync")
            try:
                if client.should_upload_keys:
                    await client.keys_upload()
                    logger.debug("Keys uploaded successfully")
                else:
                    logger.debug("No key upload needed before sending message")
            except Exception as ke:
                logger.warning(f"Error uploading keys: {ke}")

            # Perform sync AFTER key upload
            logger.debug("Performing sync AFTER key upload")
            await client.sync(timeout=3000)
            
            room = client.rooms.get(room_id) # Get room object
            if not room:
                logger.error(f"Room {room_id} not found in client.rooms. Cannot claim keys.")
                return None


            # Build a list of all devices in the room
            users_devices = {}
            for user_id_in_room in room.users:
                if user_id_in_room != client.user_id:  # Skip our own user
                    # Get all devices for this user
                    devices = client.device_store.active_user_devices(user_id_in_room)
                    if devices:
                        users_devices[user_id_in_room] = [device.device_id for device in devices]

            # Debug users_devices
            logger.debug(f"Users devices before key claim: {users_devices}")

            # Claim keys for all devices
            if users_devices:
                logger.debug(f"Claiming keys for {len(users_devices)} users in room {room_id}")
                try:
                    await client.keys_claim(users_devices)
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
                    client.load_store()
                    logger.debug("Encryption store loaded successfully")
                except Exception as le:
                    logger.warning(f"Error loading encryption store: {le}")

                # Implement exponential backoff retry for sharing group session
                max_attempts = 3
                for attempt in range(max_attempts):
                    try:
                        # Always use ignore_unverified_devices=True to ensure messages can be sent
                        await client.share_group_session(room_id, ignore_unverified_devices=True)
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
                await client.sync(timeout=3000)  # 3 second timeout

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
                        await client.keys_upload()

                        # Try to share with retry for Already sharing errors
                        try:
                            await client.share_group_session(room_id, ignore_unverified_devices=True)
                        except Exception as retry_error:
                            if "Already sharing a group session" in str(retry_error):
                                logger.debug("Recovery detected group session already being shared, continuing")
                            else:
                                raise

                        # Perform a short sync to ensure the group session is properly registered
                        logger.debug("Performing short sync to update encryption state...")
                        await client.sync(timeout=3000)  # 3 second timeout

                        logger.debug("Recovery successful, shared new group session")
                    except Exception as recovery_error:
                        logger.error(f"Recovery failed: {recovery_error}")
    except Exception as e:
        logger.error(f"Error preparing encryption: {e}")
        return None # Return None on failure

    # Send the message with a timeout and retry logic
    max_retries = 3
    response = None

    for attempt in range(1, max_retries + 1):
        try:
            response = await asyncio.wait_for(
                client.room_send(
                    room_id=room_id,
                    message_type=message_type,
                    content=content,
                    ignore_unverified_devices=True,  # Important: ignore unverified devices
                ),
                timeout=10.0,  # Increased timeout
            )
            logger.debug(f"Message sent successfully to room {room_id} on attempt {attempt}")
            return response # Return response on success
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
                await client.sync(timeout=3000)
                await asyncio.sleep(1.0 * attempt)  # Backoff
                continue
            else:
                # Re-raise on last attempt if it's not an OlmUnverifiedDeviceError
                if not isinstance(send_error, exceptions.OlmUnverifiedDeviceError):
                    raise
                else: # Handle OlmUnverifiedDeviceError specifically
                    logger.warning(
                        f"Encryption error with unverified device in room {room_id}: {send_error}"
                    )
                    logger.warning(
                        "Using ignore_unverified_devices and sharing a new group session..."
                    )
                    try:
                        room = client.rooms.get(room_id)
                        if client.olm and client.device_store and room:
                            logger.debug(f"Using ignore_unverified_devices for room {room_id}")

                        users_devices = {}
                        for user_id_in_room in room.users:
                            if user_id_in_room != client.user_id:
                                devices = client.device_store.active_user_devices(user_id_in_room)
                                if devices:
                                    users_devices[user_id_in_room] = [device.device_id for device in devices]
                        
                        if users_devices:
                            logger.debug(f"Querying keys for users in room {room_id} after error: {list(users_devices.keys())}")
                            try:
                                await client.keys_query(list(users_devices.keys()))
                                logger.debug("Keys query completed successfully after error")
                            except Exception as ke:
                                logger.warning(f"Error querying keys after error: {ke}")

                            logger.debug(f"Claiming keys for devices in room {room_id} after error")
                            try:
                                await client.keys_claim(users_devices)
                                logger.debug("Keys claim completed successfully after error")
                            except Exception as ke:
                                logger.warning(f"Error claiming keys after error: {ke}")
                        
                        if client.olm:
                            await client.share_group_session(room_id, ignore_unverified_devices=True)
                            logger.debug(f"Shared new group session for room {room_id} with ignore_unverified_devices=True")

                        response = await asyncio.wait_for(
                            client.room_send(
                                room_id=room_id,
                                message_type=message_type,
                                content=content,
                                ignore_unverified_devices=True,
                            ),
                            timeout=10.0,
                        )
                        logger.info(f"Successfully sent message with ignore_unverified_devices=True to room: {room_id}")
                        return response # Return response on success
                    except Exception as retry_error:
                        logger.error(f"Failed to send message even with ignore_unverified_devices=True: {retry_error}")
                        return None # Return None on failure
    return None # Return None if all retries fail


async def handle_decryption_failure(client: AsyncClient, room: MatrixRoom, event: MegolmEvent):
    logger.warning(
        f"Received encrypted event that could not be decrypted in room {room.room_id}"
    )
    try:
        if client.olm and client.device_store:
            sender = event.sender
            logger.info(
                f"Attempting to handle undecryptable event from {sender}"
            )

            # We don't verify other users' devices - we use ignore_unverified_devices instead
            logger.debug(f"Using ignore_unverified_devices for {sender}'s devices")

            # 2. Upload our keys
            if client.should_upload_keys:
                await client.keys_upload()
                logger.debug(f"Uploaded keys for {sender}")

            # 3. Request keys from the sender
            try:
                # Request keys from the sender's devices
                user_devices = {}
                user_devices[sender] = [
                    device.device_id
                    for device in client.device_store.active_user_devices(
                        sender
                    )
                ]
                if user_devices[sender]:
                    logger.debug(
                        f"Requesting keys from {sender}'s devices: {user_devices[sender]}"
                    )
                    await client.keys_claim(user_devices)
                    logger.debug(f"Claimed keys from {sender}")
            except Exception as key_error:
                logger.warning(f"Error claiming keys: {key_error}")

            # 4. Force a sync to get updated keys
            logger.debug("Forcing sync to get updated keys")
            await client.sync(timeout=5000)

            # 5. Try to decrypt the event again
            if hasattr(client, "decrypt_event") and callable(
                client.decrypt_event
            ):
                try:
                    logger.debug("Attempting to decrypt the event again")
                    decrypted_event = await client.decrypt_event(event) # nio's decrypt_event returns a new event or None
                    if decrypted_event: # Check if decryption was successful
                        logger.info(
                            "Successfully decrypted event after key claim!"
                        )
                        # Caller will need to re-process the event if decryption succeeds.
                        # For now, this function's scope is just to attempt decryption.
                        return True # Indicate success
                    else:
                        logger.warning("Failed to decrypt event after key claim and sync.")
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
    # Note: get_e2ee_store_dir() is imported from mmrelay.config
    logger.info(f"Current store directory: {get_e2ee_store_dir()}") 
    logger.info(
        "You can also try logging out and back in to your Matrix client."
    )
    return False # Indicate failure
