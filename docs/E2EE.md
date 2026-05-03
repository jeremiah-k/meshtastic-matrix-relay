# Matrix End-to-End Encryption (E2EE) Guide

MMRelay can participate in **encrypted Matrix rooms** using Matrix End-to-End
Encryption (E2EE).

> **Important**: MMRelay bridges Meshtastic and Matrix. Messages are
> **decrypted/re-encrypted at the relay** when crossing between platforms.
> "E2EE support" here means **MMRelay can join and operate in encrypted Matrix
> rooms** (like any other Matrix client), not that messages stay end-to-end
> encrypted across the entire Meshtastic <-> Matrix path.

## E2EE provider

MMRelay uses [**mindroom-nio**](https://github.com/mindroom-ai/mindroom-nio) as
its Matrix SDK with **vodozemac** (a Rust cryptographic library) for E2EE. Vodozemac is the successor to the deprecated
libolm and is distributed as precompiled wheels — no additional system
libraries are needed.

The legacy `matrix-nio` provider (with `python-olm`) is still technically
usable if you manually replace mindroom-nio, but upstream matrix-nio is mostly
inactive and mindroom-nio is recommended for all deployments.

### Legacy matrix-nio provider

mindroom-nio is the default and recommended provider. Legacy matrix-nio
remains supported only by manually replacing mindroom-nio.

**Do not install both providers in the same Python environment.** They both
provide the `nio` namespace and will conflict. Do not use `mmrelay[e2e]` for
legacy matrix-nio, because the `e2e` extra always installs mindroom-nio.

To switch to the legacy provider:

```bash
pip uninstall mindroom-nio
pip install 'matrix-nio[e2e]==0.25.2'
```

## Index

- [E2EE provider](#e2ee-provider)
- [What E2EE means in MMRelay](#what-e2ee-means-in-mmrelay)
- [How MMRelay handles encrypted rooms](#how-mmrelay-handles-encrypted-rooms)
- [Security considerations](#security-considerations)
- [Requirements and platform support](#requirements-and-platform-support)
- [Quick start](#quick-start)
- [The `auth login` command](#the-auth-login-command)
- [File locations](#file-locations)
- [Device verification status](#device-verification-status)
- [Troubleshooting](#troubleshooting)
- [Backward compatibility](#backward-compatibility)
- [Technical details](#technical-details)

## What E2EE means in MMRelay

When E2EE is enabled:

- Messages from Meshtastic are encrypted before being sent to encrypted Matrix
  rooms
- Encrypted messages from Matrix are decrypted before being relayed to
  Meshtastic
- MMRelay maintains its own device identity and encryption keys
- Encrypted and unencrypted rooms can run side by side in the same relay

### What this means

- On the **Matrix side**, messages are encrypted using the Olm/Megolm protocol
  (backed by vodozemac) between MMRelay and other Matrix clients in the room.
- MMRelay behaves like a normal Matrix client in encrypted rooms: it stores
  keys, requests keys when needed, and decrypts/encrypts messages as needed.

### What this does not mean

- It does **not** provide end-to-end encryption from a Meshtastic device all
  the way to a Matrix user.

### Security implications

- MMRelay decrypts and re-encrypts at the bridge boundary, so the relay host
  and OS account running MMRelay are part of your trusted computing base.
- Combining Meshtastic and Matrix can expand your attack surface; apply each
  platform's security practices.

See [Security considerations](#security-considerations) for details.

## How MMRelay handles encrypted rooms

### Room encryption detection

MMRelay automatically detects room encryption status:

- **Encrypted rooms**: outgoing messages are encrypted before send
- **Unencrypted rooms**: messages are sent as plaintext
- **Mixed environments**: each room is handled based on its own state

### Device and key lifecycle

MMRelay manages encryption devices automatically:

- Keeps a consistent device ID across restarts
- Stores encryption keys in `~/.mmrelay/matrix/store/`
- Uploads encryption keys when needed
- Uses `ignore_unverified_devices=True` for reliable room operation
- Automatically requests missing room keys when an event cannot be decrypted

### Message flow

1. **Outgoing (Meshtastic -> Matrix)**
   - MMRelay receives a message from Meshtastic
   - Checks whether the target Matrix room is encrypted
   - Encrypts if needed, then sends to Matrix
2. **Incoming (Matrix -> Meshtastic)**
   - MMRelay receives a Matrix message (encrypted or plaintext)
   - If encrypted and key is missing, MMRelay requests the key
   - On a later sync, MMRelay decrypts and forwards to Meshtastic

## Security considerations

### Trust model

MMRelay bridges Meshtastic and Matrix:

- Meshtastic -> Matrix: MMRelay reads the Meshtastic payload (as provided by
  the connected node) and then sends an encrypted Matrix event (for encrypted
  rooms)
- Matrix -> Meshtastic: MMRelay decrypts Matrix events (for encrypted rooms)
  and then emits a Meshtastic message via the connected node

### Meshtastic encryption is separate

Meshtastic link/channel encryption (and its limitations) are independent of
Matrix E2EE.

- MMRelay does not change Meshtastic encryption behavior; it connects to a node
  and uses whatever channels/keys that node is configured with.
- If your threat model includes channel key leakage, a compromised node/app, or
  insecure distribution of channel keys, MMRelay cannot fix that (and may
  expand your attack surface by adding another host that must be protected).

### Credentials and key storage

The Matrix credentials and encryption keys are equivalent to any other Matrix
client session:

- `~/.mmrelay/matrix/credentials.json` - Matrix login credentials (access
  token, device ID)
- `~/.mmrelay/matrix/store/` - Matrix encryption keys for this session

If these are lost or compromised, the impact is the same as losing any Matrix
session: you need to log in again and re-verify devices.

### Recommendations

- Limit access to `~/.mmrelay/` to the OS user running MMRelay
- If credentials/store are compromised, log out the device on your Matrix
  server and run `mmrelay auth login` again
- Treat this relay host as a high-trust system in your deployment model

## Requirements and platform support

- **Python 3.10 or higher**
- **Linux or macOS** (E2EE is not supported on Windows due to library
  limitations)
- **MMRelay v1.2+** with E2EE support:

```bash
pipx install 'mmrelay[e2e]'
```

The `mmrelay[e2e]` extra installs **mindroom-nio[e2e]** which includes the
**vodozemac** crypto backend (Rust-based, distributed as precompiled wheels
requiring no additional system libraries).

### Windows limitation

**E2EE is not available on Windows** due to technical limitations with required
cryptographic libraries. Vodozemac and the legacy python-olm backend both
depend on native libraries that are not straightforward to install on Windows.

Windows users can still use MMRelay for regular (unencrypted) Matrix
communication.

## Quick start

### 1. Install E2EE support

```bash
pipx install 'mmrelay[e2e]'

# Or using pip
pip install 'mmrelay[e2e]'
```

This installs **mindroom-nio** with the **vodozemac** crypto backend as the
default E2EE provider.

### 2. Enable E2EE in config

Add E2EE settings to `~/.mmrelay/config.yaml`:

```yaml
matrix:
  homeserver: https://your-matrix-server.org

  e2ee:
    enabled: true
    # Optional: defaults to ~/.mmrelay/matrix/store
    # store_path: ~/.mmrelay/matrix/store
```

### 3. Authenticate

```bash
mmrelay auth login
```

This interactive command prompts for homeserver, username, and password, then
creates persistent credentials and initializes the E2EE key store.

### 4. Start MMRelay

```bash
mmrelay
```

MMRelay will encrypt for encrypted rooms and decrypt incoming encrypted events.
The first encrypted message from a newly seen device may briefly fail to decrypt
until key exchange completes on a subsequent sync.

## The `auth login` command

`mmrelay auth login` is the recommended Matrix authentication flow in MMRelay
v1.2+ and supports E2EE setup end to end.

### What it does

1. Prompts for Matrix homeserver, username, and password
2. Creates a Matrix session with encryption enabled
3. Generates/uses a persistent device ID for MMRelay
4. Stores credentials at `~/.mmrelay/matrix/credentials.json`
5. Initializes key storage at `~/.mmrelay/matrix/store/`

### Example session

```bash
$ mmrelay auth login
Matrix Bot Login for E2EE
=========================
Matrix homeserver (e.g., https://matrix.org): https://matrix.example.org
Matrix username (localpart or full user ID, e.g., bot or @bot:example.com): @mmrelay-bot:matrix.example.org
Matrix password: [password hidden]

Login successful!
Device ID: MMRELAY_ABC123DEF
Credentials saved to ~/.mmrelay/matrix/credentials.json
E2EE store initialized at ~/.mmrelay/matrix/store/

You can now start MMRelay with: mmrelay
```

### Logging out

To clear Matrix session data and keys:

```bash
mmrelay auth logout
```

This verifies your Matrix password, logs out from the homeserver (invalidating
access tokens), removes credentials, and clears local E2EE key storage.

## File locations

### Configuration and state

- **Main config**: `~/.mmrelay/config.yaml`
- **Matrix credentials**: `~/.mmrelay/matrix/credentials.json`
- **Encryption store**: `~/.mmrelay/matrix/store/`

### `credentials.json` format

```json
{
  "homeserver": "https://your-matrix-server.org",
  "user_id": "@your-bot:your-matrix-server.org",
  "access_token": "your_access_token_here",
  "device_id": "MMRELAY_DEVICE_ID"
}
```

Keep this file secure; it contains Matrix session credentials.

## Device verification status

In Matrix clients (Element, etc.), MMRelay messages in encrypted rooms are
expected to show a red shield warning:

### "Encrypted by a device not verified by its owner"

This is expected because:

- Messages **are encrypted** using Matrix E2EE (Olm/Megolm via vodozemac)
- The nio library does not support interactive device verification
  (emoji/QR verification)
- MMRelay devices cannot be cross-signed through the standard Matrix client
  verification flow

If you see **"Not encrypted"** for MMRelay messages in an encrypted room, treat
that as a real issue (usually configuration/version related) and troubleshoot.

## Troubleshooting

### "E2EE features not available on Windows"

**Problem**: E2EE features do not work on Windows.

**Explanation**: E2EE requires native cryptographic libraries (vodozemac for
mindroom-nio). Both vodozemac and the legacy python-olm have native
dependencies that are difficult to install on Windows.

**What to do**:

- Use Linux or macOS for E2EE
- On Windows, use MMRelay for regular Matrix communication

### "No E2EE dependencies found"

Install E2EE dependencies (Linux/macOS):

```bash
pipx install 'mmrelay[e2e]'

# Or using pip
pip install 'mmrelay[e2e]'
```

This installs mindroom-nio with the vodozemac crypto backend. Vodozemac is
distributed as precompiled Rust wheels requiring no extra system libraries.

If running from a local checkout:

```bash
pip install -e '.[e2e]'
```

> **Warning**: Do not install both mindroom-nio and matrix-nio in the same
> environment. They both provide the `nio` namespace and will conflict.

### "Failed to decrypt event" in logs

**Problem**: You see `ERROR Matrix: Failed to decrypt event...`.

**Explanation**: MMRelay could not decrypt the event with the keys currently available.
This can happen when required room keys are unavailable, session data is stale, or
key sharing has not completed.

**What to do**:

- Check subsequent logs for key-request activity; MMRelay requests missing keys automatically
- Let sync complete, then verify whether decryption succeeds on the next event pass
- If failures persist, reset session data:

```bash
mmrelay auth logout
mmrelay auth login
```

### Verify startup state

Look for logs like:

```bash
INFO Matrix: Found credentials at ~/.mmrelay/matrix/credentials.json
INFO Matrix: Using device ID: YOUR_DEVICE_ID
INFO Matrix: Setting up End-to-End Encryption...
INFO Matrix: Encryption keys uploaded successfully
INFO Matrix: Performing initial sync to initialize rooms...
INFO Matrix: Initial sync completed. Found X rooms.
```

## Backward compatibility

E2EE support is backward compatible:

- Existing setups continue to work
- Mixed encrypted/unencrypted room setups are supported
- E2EE remains optional via `e2ee.enabled: false`

## Technical details

### Implementation

- Uses **mindroom-nio** with the **vodozemac** crypto backend (the legacy
  matrix-nio / python-olm stack is also technically supported but unmaintained).
- Both providers implement the same Matrix **Olm/Megolm** encryption protocols;
  they differ only in the underlying cryptographic library.
- Loads E2EE store before sync operations
- Uses automatic key management with `ignore_unverified_devices=True`
- Provider detection and capability reporting via `mmrelay.matrix.compat`

### Performance impact

E2EE overhead is generally small:

- Slightly longer startup due to key sync
- Negligible per-message encryption/decryption latency
- Small memory increase for key material
- Additional sync traffic for key management
