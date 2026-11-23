# What's new in MMRelay 1.5.0

MMRelay 1.5 adds shared-room interoperability metadata to radio messages
sent to Matrix, tightens radio and process lifecycle handling, and requires
Python 3.12 or later. The visible message text is unchanged. MMRelay still
owns its Meshtastic and Matrix connections; its MEDRE dependencies are Python
libraries, **not** a separate service to install or run.

**Most installations need no config-file changes.** Configure trusted peer
bots only if your Matrix room is also bridged by another MMRelay instance.
Even without shared rooms, Matrix events claiming MEDRE relay metadata are
subject to the trust check; ordinary Matrix text remains unaffected. See
[Sharing rooms with other MMRelay instances](#sharing-rooms-with-other-mmrelay-instances).

## About MEDRE

[MEDRE](https://github.com/jeremiah-k/medre) is a separate, developing
bridge project for connecting Meshtastic to other mesh networks and
messaging services such as MeshCore, LXMF, and XMPP. For those connections,
MEDRE is stable enough for testing; consult MEDRE's own documentation for
the current support status. If you only bridge Meshtastic and Matrix,
MMRelay on its own is the best option. MEDRE is still taking shape and does
not have a plugin system yet.

MMRelay uses MEDRE's published event format and Matrix adapter to attach
interoperability metadata to radio messages posted to Matrix. This does
not change the displayed text or start another Matrix or radio session.
The required MEDRE Python packages install with MMRelay.

## Highlights

- **Shared-room interoperability.** Between MMRelay instances that trust
  each other in a shared room, peer-reported radio sender names can be
  preserved, and replies and reactions retain their targets when the
  relevant message mappings exist. Peer attribution is not independently
  authenticated as the original radio sender.
- **More predictable shutdown and restart.** Radio reconnects, the outbound
  message queue, plugin reloads, and delayed subscriptions are protected
  against overlapping or late work.
- **Better radio identity and connection handling.** Sender identities are
  matched consistently; stable serial device aliases and mapped character
  devices are supported. Weather requests no longer require the sender to
  already exist in the node database.
- **Updated dependencies.** Python **3.12 or later** is required, and the
  Matrix SDK and related libraries are updated. Required MEDRE dependencies
  install with MMRelay; **native Linux/macOS encrypted-room installs still
  require the optional `mmrelay[e2e]` extra**.

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
- On native Linux/macOS, install `mmrelay[e2e]` to use encrypted Matrix
  rooms; the base MMRelay package alone does not install the optional E2EE
  dependencies. See the [E2EE guide](E2EE.md#supported-provider).
- Preserve `matrix/credentials.json` and the **complete** `matrix/store/`
  directory with the same bot account/device. Don't delete stores or reset
  cross-signing merely to complete an upgrade.

### Sharing rooms with other MMRelay instances

These changes apply only when the same Matrix room is bridged by more than
one MMRelay instance.

**All MMRelay instances sharing a Matrix room must run 1.5.0 or later
and trust each other.** Configure `matrix.trusted_relays` on **each** relay
with the exact Matrix user ID of **every other** relay bot in that room.
For two instances, configure both directions:

On relay A (`@relay-a:example.org`):

```yaml
matrix:
  trusted_relays:
    - "@relay-b:example.org"
```

On relay B (`@relay-b:example.org`):

```yaml
matrix:
  trusted_relays:
    - "@relay-a:example.org"
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
- Ordinary Matrix messages **without** the reserved MEDRE claim continue to
  relay as before, including messages from human users. A user who sends a
  MEDRE claim is subject to the same trust check as a peer bot.
- **Do not mix older relay versions into a shared-room deployment.**
  Upgrade every participating instance and configure mutual trust before
  using the room for cross-mesh relaying.

If you see one of these warnings, use the **observed Matrix sender ID**
in the log to check the peer account before updating `matrix.trusted_relays`.
Warnings repeat at most once every five minutes **per sender and rejection
reason** in a running process; further drops are logged at DEBUG. The
rejection logs omit message bodies and raw envelopes. Every rejected claim
is dropped, whether or not it generates another warning.

**Avoid message loops:** do not connect two relays to the same Meshtastic
mesh and Matrix room unless you have separately tested how loops are
prevented in your topology. Mutual trust is required for shared-room peer
handling, **but it is not a general-purpose loop-prevention mechanism**;
incorrect room or radio topology can still cause repeated traffic.

**Radio identity and privacy:** Relay events may carry stable Meshtastic node
IDs in Matrix event metadata. People and clients with access to the room can
correlate those IDs across Matrix rooms and radio meshes, even if the IDs are
also broadcast over radio. Treat them as privacy-relevant. Trusting a peer's
Matrix bot account does **not** authenticate its claimed radio sender, verify
that the displayed radio name is genuine, or prove delivery to a radio node.

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

The required MEDRE packages install automatically with MMRelay; there is
no separate MEDRE service to run. When upgrading a container deployment,
select the release image tag explicitly (or pin its verified digest) and
keep your existing settings — the
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
