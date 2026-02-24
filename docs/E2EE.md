# Matrix End-to-End Encryption (E2EE) Guide

MMRelay can participate in **encrypted Matrix rooms** using Matrix End-to-End Encryption (E2EE). This guide covers how to enable that support.

> **Important**: MMRelay bridges Meshtastic and Matrix. Messages are **decrypted/re-encrypted at the relay** when crossing between platforms. "E2EE support" here means **MMRelay can join and operate in encrypted Matrix rooms** (like any other Matrix client), not that messages stay end-to-end encrypted across the entire Meshtastic ↔ Matrix path.

## E2EE in MMRelay

MMRelay can participate in encrypted Matrix rooms. When E2EE is enabled:

- Messages from Meshtastic are encrypted before being sent to encrypted Matrix rooms
- Encrypted messages from Matrix are decrypted before being relayed to Meshtastic
- MMRelay maintains its own device identity and encryption keys
- Both encrypted and regular rooms work seamlessly in the same relay

### What this does (and does not) mean

**What it means:**

- On the **Matrix side**, messages are protected using Matrix E2EE (Olm/Megolm) between MMRelay and other Matrix clients in the room.
- MMRelay behaves like a normal Matrix client in encrypted rooms: it stores keys, requests keys when needed, and decrypts/encrypts messages as appropriate.

**What it does NOT mean:**

- It does **not** provide end-to-end encryption from a Meshtastic device all the way to a Matrix user.
- The relay host is part of the trusted computing base: it sees message plaintext when translating between platforms.
- Security limitations of each platform still apply, and combining them can add risk.

## Quick Start

### 1. Install E2EE Support

```bash
# Install MMRelay with E2EE support (recommended)
pipx install 'mmrelay[e2e]'

# Or using pip
pip install 'mmrelay[e2e]'
```

### 2. Enable E2EE in Configuration

Add E2EE configuration to your `~/.mmrelay/config.yaml`:

```yaml
matrix:
  homeserver: https://your-matrix-server.org

  # E2EE Configuration
  e2ee:
    enabled: true
    # Optional: Custom store path (default: ~/.mmrelay/matrix/store)
    # store_path: ~/.mmrelay/matrix/store
```

### 3. Set Up Authentication

Use the built-in authentication command to create your bot's E2EE-enabled credentials:

```bash
mmrelay auth login
```

This interactive command will:

- Prompt for your Matrix homeserver, username, and password
- Create secure credentials with E2EE support
- Save authentication details to `~/.mmrelay/matrix/credentials.json`
- Set up encryption keys for secure communication

### 4. Start MMRelay

```bash
mmrelay
```

That's it! MMRelay will automatically encrypt messages for encrypted rooms and decrypt incoming encrypted messages. The first time it sees an encrypted message from a new device, it may log a "Failed to decrypt" error, but it will automatically request the necessary keys and decrypt the message on the next sync.

## Requirements

- **Python 3.10 or higher**
- **Linux or macOS** (E2EE is not supported on Windows due to library limitations)
- **MMRelay v1.2+** with E2EE support: `pipx install 'mmrelay[e2e]'`

### Windows Limitation

**E2EE is not available on Windows** due to technical limitations with the required cryptographic libraries. The `python-olm` library requires native C libraries that are difficult to compile and install on Windows systems.

**Windows users can still use MMRelay** for regular (unencrypted) Matrix communication. Use `mmrelay auth login` to create credentials.

## The `auth login` Command

The `auth login` command is the recommended way to set up Matrix authentication for MMRelay v1.2+. It provides secure credential management with full E2EE support.

### What It Does

```bash
mmrelay auth login
```

**The authentication process:**

1. **Interactive Setup**: Prompts for Matrix homeserver, username, and password
2. **Secure Login**: Creates a new Matrix session with encryption enabled
3. **Device Registration**: Generates a unique, persistent device ID for MMRelay
4. **Credential Storage**: Saves authentication details to `~/.mmrelay/matrix/credentials.json`
5. **Key Setup**: Initializes encryption key storage in `~/.mmrelay/matrix/store/`

### Example Session

```bash
$ mmrelay auth login
Matrix Bot Login for E2EE
=========================
Matrix homeserver (e.g., https://matrix.org): https://matrix.example.org
Matrix username (e.g., @user:matrix.org): @mmrelay-bot:matrix.example.org
Matrix password: [password hidden]

✅ Login successful!
✅ Device ID: MMRELAY_ABC123DEF
✅ Credentials saved to ~/.mmrelay/matrix/credentials.json
✅ E2EE store initialized at ~/.mmrelay/matrix/store/

You can now start MMRelay with: mmrelay
```

### Logging Out

To completely log out and clear all Matrix session data:

```bash
mmrelay auth logout
```

This command will:

- Verify your Matrix password for security
- Log out from the Matrix server (invalidating access tokens)
- Remove `~/.mmrelay/matrix/credentials.json`
- Clear the E2EE store directory (`~/.mmrelay/matrix/store/`)
- Provide confirmation of all cleanup actions

After logout, you'll need to run `mmrelay auth login` again to re-authenticate.

### Files Created

**`~/.mmrelay/matrix/credentials.json`** - Contains your Matrix session:

```json
{
  "homeserver": "https://matrix.example.org",
  "user_id": "@mmrelay-bot:matrix.example.org",
  "access_token": "your_access_token_here",
  "device_id": "MMRELAY_ABC123DEF"
}
```

**`~/.mmrelay/matrix/store/`** - Directory containing encryption keys and device information (multiple database files).

## How It Works

### Automatic Encryption Detection

MMRelay automatically detects room encryption status:

- **Encrypted rooms**: Messages are automatically encrypted before sending
- **Unencrypted rooms**: Messages are sent as normal plaintext
- **Mixed environments**: Each room is handled according to its encryption status

### Device Management

MMRelay manages encryption devices automatically:

- **Consistent Device ID**: Maintains the same device identity across restarts
- **Key Storage**: Encryption keys are stored securely in `~/.mmrelay/matrix/store/`
- **Automatic Key Sharing**: When the bot sees an encrypted message it can't read, it automatically requests the necessary keys from other clients in the room.
- **Device Trust**: Uses `ignore_unverified_devices=True` for reliable operation
- **Key Upload**: Automatically uploads encryption keys when needed

### Message Flow

1. **Outgoing Messages** (Meshtastic → Matrix):
   - MMRelay receives message from Meshtastic device
   - Checks if target Matrix room is encrypted
   - If encrypted: Encrypts message using room's encryption keys
   - Sends encrypted message to Matrix room

2. **Incoming Messages** (Matrix → Meshtastic):
   - MMRelay receives an encrypted message from a Matrix room.
   - If it cannot be decrypted, the bot automatically requests the key.
   - On a subsequent sync, the bot receives the key and decrypts the message.
   - Forwards decrypted message to Meshtastic device.

## Security Considerations

### Trust model

MMRelay bridges Meshtastic and Matrix:

- Meshtastic → Matrix: MMRelay reads the Meshtastic payload (as provided by the connected node) and then sends an encrypted Matrix event (for encrypted rooms).
- Matrix → Meshtastic: MMRelay decrypts Matrix events (for encrypted rooms) and then emits a Meshtastic message via the connected node.

That means the **relay host** (and the OS user running it) can access message plaintext, and anyone who can read the MMRelay config/credentials/store can impersonate the bot.

### Meshtastic encryption is separate

Meshtastic link/channel encryption (and its limitations) are independent of Matrix E2EE.

- MMRelay does not change how Meshtastic encryption works; it connects to a node and uses whatever channels/keys that node is configured with.
- If your threat model includes things like **channel key leakage**, a compromised node/app, or insecure distribution of channel keys, MMRelay can't fix that (and may expand your attack surface by adding another host that must be protected).

### Credentials and key storage

The Matrix credentials and encryption keys are equivalent to any other Matrix client session:

- `~/.mmrelay/matrix/credentials.json` — Matrix login credentials (access token, device ID)
- `~/.mmrelay/matrix/store/` — Matrix encryption keys for this session

If these are lost or compromised, the impact is the same as losing any Matrix session: you'd need to log in again and re-verify devices. Protect these files with appropriate file permissions, but they don't require special backup procedures or long-term archival.

### Recommendations

- Limit access to `~/.mmrelay/` to the OS user running MMRelay
- If the credentials or store are compromised, log out the device from your Matrix server and re-run `mmrelay auth login`
- Understand that combining platforms may expand your attack surface — each platform's security limitations still apply

## File Locations

### Configuration Files

- **Main Config**: `~/.mmrelay/config.yaml`
- **E2EE Credentials**: `~/.mmrelay/matrix/credentials.json`
- **Encryption Store**: `~/.mmrelay/matrix/store/` (directory)

### Credentials File Format

The `credentials.json` file contains:

```json
{
  "homeserver": "https://your-matrix-server.org",
  "user_id": "@your-bot:your-matrix-server.org",
  "access_token": "your_access_token_here",
  "device_id": "MMRELAY_DEVICE_ID"
}
```

**Important**: Keep this file secure as it contains your Matrix access credentials.

## Device Verification Status

In your Matrix client (Element, etc.), MMRelay's messages will show as encrypted but with a warning about unverified devices. This is expected:

- **Encrypted messages**: Show a red shield with "Encrypted by a device not verified by its owner"
- **Unencrypted messages**: Show a red shield with "Not encrypted" warning

**Why devices appear unverified**: Matrix clients use interactive verification (emoji comparisons, QR codes) to confirm device identity. The matrix-nio library doesn't support this verification protocol, so MMRelay devices cannot be verified. The messages are still encrypted — this just means you can't cryptographically confirm the device identity through the usual Matrix verification flow.

If messages show as completely unencrypted in encrypted rooms, check your MMRelay version and configuration.

## Troubleshooting

### Common Issues

#### "E2EE features not available on Windows"

**Problem**: E2EE features don't work on Windows even with `mmrelay auth login`.

**Explanation**: E2EE requires the `python-olm` library, which depends on native C libraries that are difficult to compile on Windows.

**Solutions**:

- **Use Linux or macOS** for full E2EE support
- **On Windows**: `mmrelay auth login` works for regular Matrix communication

**Note**: Credentials created with `mmrelay auth login` on Windows will work with E2EE if you later use them on Linux/macOS.

#### "No E2EE dependencies found"

**Solution**: Install E2EE dependencies (Linux/macOS only):

```bash
pipx install 'mmrelay[e2e]'
```

If running from a local checkout (editable install), use:

```bash
pip install -e '.[e2e]'
```

#### "Failed to decrypt event" error in logs

**Problem**: You see `ERROR Matrix: Failed to decrypt event...` in your logs.

**Explanation**: This is usually normal, temporary behavior. It happens when another user sends a message in an encrypted room and the relay doesn't have the decryption key for it yet.

**Solution**:

- **Wait**: The relay will automatically request the key in the background. The message should be successfully decrypted within the next minute during the next sync from the server.
- **If the error persists for a long time**: This might indicate a de-synchronized session. The best way to fix this is to regenerate your credentials and key store.

  ```bash
  # Remove old credentials and store
  mmrelay auth logout

  # Create new credentials
  mmrelay auth login
  ```

### Verification and Testing

#### Check E2EE Status

Look for these log messages when MMRelay starts:

```bash
INFO Matrix: Found credentials at ~/.mmrelay/matrix/credentials.json
INFO Matrix: Using device ID: YOUR_DEVICE_ID
INFO Matrix: Setting up End-to-End Encryption...
INFO Matrix: Encryption keys uploaded successfully
INFO Matrix: Performing initial sync to initialize rooms...
INFO Matrix: Initial sync completed. Found X rooms.
```

## Backward Compatibility

E2EE support is fully backward compatible:

- **Existing setups**: Continue to work without changes
- **Mixed environments**: Can handle both encrypted and unencrypted rooms
- **Optional feature**: E2EE can be disabled by setting `e2ee.enabled: false`

## Technical Details

### Implementation

- Uses matrix-nio library with Olm/Megolm encryption protocols
- E2EE store loaded before sync operations for proper initialization
- Automatic key management with `ignore_unverified_devices=True`

> **Note**: The Olm/Megolm encryption library (libolm) was deprecated in July 2024. Matrix.org considers it safe for practical use but recommends migrating to vodozemac. matrix-nio (which MMRelay uses) still depends on libolm, though migration work is in progress. We'll continue using the current encryption while it remains supported and stable.

### Performance Impact

E2EE adds minimal overhead:

- **Startup time**: Slightly longer due to key synchronization
- **Message latency**: Negligible encryption/decryption time
- **Memory usage**: Small increase for key storage
- **Network usage**: Additional sync traffic for key management
