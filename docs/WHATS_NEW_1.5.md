# What's new in MMRelay 1.5.0

MMRelay 1.5 changes how messages are formatted in Matrix rooms shared with
other MMRelay instances, tightens radio and process lifecycle handling, and
requires Python 3.12 or later. MMRelay still runs one Meshtastic connection
and one Matrix session; the MEDRE support is a bundled library, not a
separate service to install or run.

**Most installations need no configuration changes.** The shared-room
changes matter only if one of your Matrix rooms is also bridged by another
MMRelay instance — see
[Sharing rooms with other MMRelay instances](#sharing-rooms-with-other-mmrelay-instances).

## About MEDRE

[MEDRE](https://github.com/jeremiah-k/medre) is a companion project for
bridging Meshtastic to other mesh networks and messaging services such as
MeshCore, LXMF, and XMPP. If you want to make those connections, MEDRE is
stable enough for testing. If you only bridge Meshtastic and Matrix,
MMRelay on its own is the best option. MEDRE is still taking shape and does
not have a plugin system yet.

MMRelay uses the MEDRE message format for radio messages it posts to
Matrix, with the format riding along as event metadata: the visible message
text is unchanged, and the MEDRE packages install automatically with
MMRelay.

## Highlights

- **Shared-room interoperability.** Between MMRelay instances that trust
  each other in a shared room, messages keep the original sender's radio
  name, and replies and reactions still match up on the receiving side.
- **More predictable shutdown and restart.** Radio reconnects, the outbound
  message queue, plugin reloads, and delayed subscriptions are protected
  against overlapping or late work.
- **Better radio identity and connection handling.** Sender identities are
  matched consistently; stable serial device aliases and mapped character
  devices are supported. Weather requests no longer require the sender to
  already exist in the node database.
- **Updated dependencies.** Python **3.12 or later** is required, and the
  Matrix SDK and related libraries are updated. Dependencies install with
  the package; nothing extra is needed.

For a step-by-step upgrade, including backups and rollback,
see [Upgrading from 1.4.1 to 1.5.0](UPGRADING_1.5.md).

## Changes requiring operator attention

### Python, SDKs and encrypted rooms

- **Python 3.11 is no longer supported.** MMRelay 1.4.1 is the last
  released version supporting it. For encrypted Matrix rooms on **macOS
  Python 3.15**, the vodozemac version MMRelay uses has no prebuilt wheel
  and may need a Rust source build. Prefer a Python version with an
  available wheel if native build tooling is not installed; see the
  [E2EE guide](E2EE.md#requirements-and-platform-support).
- MMRelay uses a single `mindroom-nio` dependency path. Do not install
  `matrix-nio` into the same environment or manually replace the selected
  SDK.
- **Windows Matrix E2EE is not supported in 1.5.0.** Native Windows and the
  Windows installer can use unencrypted Matrix rooms and OAuth
  independently of E2EE. Linux/macOS encrypted-room support remains.
- Preserve `matrix/credentials.json` and the **complete** `matrix/store/`
  directory with the same bot account/device. Don't delete stores or reset
  cross-signing merely to complete an upgrade.

### Sharing rooms with other MMRelay instances

These changes apply only when the same Matrix room is bridged by more than
one MMRelay instance.

Run 1.5.0 or later on **every** relay that shares the room, and list the
exact Matrix user ID of each peer relay bot under `matrix.trusted_relays`:

```yaml
matrix:
  trusted_relays:
    - "@peer-relay:example.org"
```

The list is empty by default, and entries must match exactly — no
wildcards, no partial names. `mmrelay config check` rejects malformed
entries.

- A message that claims to come from a relay but whose sender is not listed
  is **dropped**, with a log warning naming the sender's Matrix account.
  Verify the account really is your peer relay before adding it.
- A listed sender whose relay message is malformed is dropped too, with a
  separate warning. Check the peer relay's version rather than looking for
  a bypass.
- Ordinary Matrix users are unaffected: regular messages relay as before.
- A relay still running 1.4 sends no relay-format messages, so it looks
  like an ordinary Matrix user. A shared room only works as intended once
  every relay in it is upgraded.

If you see one of these warnings, the sender's Matrix user ID in the log
tells you what to allow or investigate. Warnings repeat at most every five
minutes per sender; further drops of the same kind are logged at DEBUG.
Rejected messages are always dropped, and the logs never contain message
content.

**Avoid message loops:** do not connect two relays to the same Meshtastic
mesh and Matrix room unless you have separately tested how loops are
prevented in your topology. Dropping untrusted relay messages keeps them
off the radio, but it cannot recognize traffic from a relay that has not
upgraded, and it cannot protect against every misconfiguration. The
trusted-relay list is not a loop-prevention mechanism.

## Additional improvements since 1.4.1

- OAuth session renewal and device-signature handling preserve the active
  Matrix session more carefully (the native OAuth flow remains
  experimental).
- Serial configuration accepts stable Linux `/dev/serial/by-id/` and
  `/dev/serial/by-path/` aliases, including mapped container devices.
- Radio sender identities are matched more consistently.
- Radio reconnects and shutdown, the outbound message queue, and the plugin
  lifecycle handle overlapping or late events more safely.
- Long remote radio messages are trimmed after the sender prefix is added,
  keeping outgoing radio payloads within size limits.
- Node-name cache migration reports when no cache data could be carried
  over.

These are targeted fixes and safeguards, **not** guarantees of exactly-once
radio delivery, crash-persistent queues, or seamless recovery from every
uncertain send.

## Packages and deployment images

MMRelay installs its MEDRE dependencies from PyPI as part of a normal
upgrade; there is nothing extra to install or run. When upgrading a
container deployment, select the release image tag explicitly (or pin its
verified digest) and keep your existing settings — the
[upgrade guide](UPGRADING_1.5.md) walks through each method.

## How 1.5.0 was tested

- A laboratory campaign across three mesh networks covered shared-room
  message fanout, channel parity, replies, untrusted relay messages, and
  an LXMF loopback.
- The full hosted CI matrix (Python 3.12–3.15), the Windows unencrypted
  packaging and install paths, and a shared-room integration test in both
  peer-trust modes all pass.

This testing does **not** certify running multiple relays on the same
Meshtastic mesh, every native radio tapback, every LXMF/XMPP operation, or
live E2EE recovery, and green CI cannot prove delivery over physical radio
hardware.
