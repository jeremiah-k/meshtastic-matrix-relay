# E2EE Implementation Notes

**This document summarizes the final, working implementation of End-to-End Encryption (E2EE) in MMRelay as of August 2025.**

## Core E2EE Implementation Logic

The final working E2EE implementation relies on four critical components to function correctly.

### 1. Initial Sync with Full State

To correctly identify encrypted rooms at startup, the initial sync with the homeserver **must** be performed with `full_state=True`. A lightweight sync (`full_state=False`) does not provide the necessary `m.room.encryption` state events, causing the client to incorrectly treat encrypted rooms as unencrypted.

**Implementation (`src/mmrelay/matrix_utils.py`):**

```python
# In connect_matrix()
sync_response = await asyncio.wait_for(
    matrix_client.sync(
        timeout=MATRIX_EARLY_SYNC_TIMEOUT, full_state=True
    ),
    timeout=MATRIX_SYNC_OPERATION_TIMEOUT,
)
```

### 2. Robust Callback Handling for Encrypted Messages

A single callback for all message types is not sufficient to handle the nuances of E2EE. The final implementation uses two separate callbacks for handling encrypted messages:

- **`on_room_message`**: This callback is registered for `RoomMessageText` and other decrypted event types. It is only triggered after a message has been successfully decrypted by `matrix-nio`.
- **`on_decryption_failure`**: This new, dedicated callback is registered for `MegolmEvent`. It is triggered when an encrypted message is received but cannot be decrypted (usually due to a missing key).

**Registration (`src/mmrelay/main.py`):**

```python
# Register the callback for successfully decrypted messages
matrix_client.add_event_callback(
    on_room_message,
    (RoomMessageText, RoomMessageNotice, RoomMessageEmote, ReactionEvent),
)
# Register the dedicated callback for decryption failures
matrix_client.add_event_callback(on_decryption_failure, (MegolmEvent,))
```

### 3. Automatic Key Requesting on Decryption Failure

When the `on_decryption_failure` callback is triggered, it is not enough to simply log the error. The client must actively request the missing decryption key from other clients in the room.

The implementation now monkey-patches the `event.room_id` (which was found to be unreliable) and then uses the standard `event.as_key_request()` method to create and send the `m.room_key_request`.

**Implementation (`src/mmrelay/matrix_utils.py`):**

```python
# In on_decryption_failure(room, event)
try:
    # Monkey-patch the event object with the correct room_id from the room object
    event.room_id = room.room_id

    request = event.as_key_request(
        matrix_client.user_id, matrix_client.device_id
    )
    await matrix_client.to_device(request)
    logger.info(f"Requested keys for failed decryption of event {event.event_id}")
except Exception as e:
    logger.error(f"Failed to request keys for event {event.event_id}: {e}")
```

### 4. Outgoing Message Formatting (`formatted_body` fix)

A validation error in `matrix-nio`'s event parser was triggered when the relay sent a plain-text message and later received it back from the server. The parser would incorrectly create a `formatted_body: None` field, which failed schema validation.

To work around this, all outgoing `m.room.message` events of `msgtype: "m.text"` sent by `matrix_relay` now **always** include a `format` and `formatted_body` key. For plain-text messages, the `body` and `formatted_body` are identical.

**Implementation (`src/mmrelay/matrix_utils.py`):**

```python
# In matrix_relay()
content = {
    "msgtype": "m.text",
    "body": plain_body,
    # ... other keys
}

# Always add format and formatted_body to avoid nio validation errors
content["format"] = "org.matrix.custom.html"
content["formatted_body"] = formatted_body
```

## Summary of E2EE Flow

1.  **Startup**: The client connects and performs a `sync(full_state=True)`, learning which rooms are encrypted. The E2EE store is loaded and keys are uploaded if needed _before_ this sync.
2.  **Outgoing Message**: `matrix_relay` sends a message from Meshtastic to Matrix. It is correctly encrypted by `nio` because the room's encrypted state is known. The message content includes a `formatted_body` to prevent parser errors.
3.  **Incoming Encrypted Message**: A `MegolmEvent` is received.
    - **If decryption succeeds**: `nio` automatically generates a `RoomMessageText` event. `on_room_message` is triggered, and the decrypted message is relayed to Meshtastic.
    - **If decryption fails**: `nio` fires the `MegolmEvent` callback. `on_decryption_failure` is triggered. The bot logs the error and sends out an `m.room_key_request`.
4.  **Key Arrival**: The key request is received by other clients, who send the key back in a `m.forwarded_room_key` to-device event. `nio` automatically processes this key and stores it.
5.  **Decryption Retry**: The next time the client syncs, it can now decrypt the original message it failed on. The `RoomMessageText` callback will be fired, and the message will be processed.
