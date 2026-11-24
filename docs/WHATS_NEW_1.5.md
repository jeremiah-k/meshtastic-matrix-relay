# What's new in MMRelay 1.5.0

MMRelay 1.5 lets rooms be shared with other relay instances, tightens radio
and process lifecycle handling, and requires Python 3.12 or later.

**Most installations need no changes.** Two cases need attention when
upgrading:

- **Python 3.11 is no longer supported.** This matters only if MMRelay runs
  directly on Python 3.11; Docker images bring their own Python. See
  [Python 3.11 is no longer supported](#python-311-is-no-longer-supported).
- **A Matrix room shared with another MMRelay instance or a MEDRE bridge.**
  Each instance needs a little extra configuration to trust the others. See
  [Sharing rooms with other relay instances](#sharing-rooms-with-other-relay-instances).

## About MEDRE

[MEDRE](https://github.com/jeremiah-k/medre) is a separate bridge project
that can also share a Matrix room with MMRelay. MMRelay uses MEDRE's message
format for radio messages it posts to Matrix, which is what lets the two
share a room: the visible message text does not change, and the needed MEDRE
packages install with MMRelay. The full introduction lives in
[About MEDRE](https://github.com/jeremiah-k/meshtastic-matrix-relay#about-medre)
in the README, and shared-room setup in
[Sharing Rooms with Other Relays](SHARED_ROOMS.md).

## Highlights

- **Shared rooms.** MMRelay instances that share a Matrix room can trust
  each other, so messages keep the sender name reported by the peer and
  replies and reactions still match up.
- **More predictable shutdown and restart.** Radio reconnects, the outbound
  message queue, plugin reloads, and delayed subscriptions are protected
  against overlapping or late work.
- **Better radio identity and connection handling.** Sender identities are
  matched consistently; stable serial device aliases and mapped character
  devices are supported. Weather requests no longer require the sender to
  already exist in the node database.
- **Updated dependencies.** Python **3.12 or later** is required, and the
  Matrix SDK and related libraries are updated.

For a step-by-step upgrade, including backups and rollback,
see [Upgrading from 1.4.1 to 1.5.0](UPGRADING_1.5.md).

## Changes requiring operator attention

### Python 3.11 is no longer supported

MMRelay 1.5.0 requires Python 3.12 or later; MMRelay 1.4.1 is the last
released version that runs on Python 3.11. Docker images bring their own
Python, so this matters only where MMRelay is installed directly, such as
with pipx on an older system. Upgrade Python before upgrading MMRelay, or
stay on 1.4.1.

If MMRelay is installed directly and uses encrypted Matrix rooms, also:

- Keep the `mmrelay[e2e]` extra when upgrading. The base package alone does
  not install the optional E2EE dependencies. See the
  [E2EE guide](E2EE.md#supported-provider).
- Keep `matrix/credentials.json` and the **complete** `matrix/store/`
  directory with the same bot account and device. Don't delete stores or
  reset cross-signing merely to complete an upgrade.
- On **macOS Python 3.15**, the vodozemac version MMRelay uses has no
  prebuilt wheel and may need a Rust source build. Prefer a Python version
  with an available wheel if native build tooling is not installed; see the
  [E2EE guide](E2EE.md#requirements-and-platform-support).
- Do not install `matrix-nio` alongside `mindroom-nio`; MMRelay uses
  `mindroom-nio` only.
- **Windows:** Matrix E2EE is not supported in 1.5.0. Unencrypted rooms and
  OAuth are unaffected.

### Sharing rooms with other relay instances

A Matrix room can be bridged by more than one relay, whether MMRelay
instances or a MEDRE bridge. Every MMRelay instance sharing a room must run
1.5.0 or later and trust every other relay in it: configure
`matrix.trusted_relays` on **each** relay with the exact Matrix user ID of
**every other** relay bot in that room. Relay claims from unlisted senders
and invalid claims from trusted peers are dropped with warnings; ordinary
Matrix messages without relay claims are unaffected.

The full how-to — trust configuration examples, the warnings you may see,
loop guidance, privacy notes, and moving an account between MMRelay and
MEDRE — lives in
[Sharing Rooms with Other Relays](SHARED_ROOMS.md).

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
