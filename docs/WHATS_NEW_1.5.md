# What's new in MMRelay 1.5.0

MMRelay 1.5 modernizes Matrix interoperability, tightens radio and process
lifecycle handling, and updates the supported Python and Matrix SDK versions.
It **does not** replace MMRelay's transport sessions with a MEDRE daemon.

## Highlights

- **Shared-room MEDRE interoperability.** Radio messages sent to Matrix use a
  canonical MEDRE envelope and generic bridge context. Incoming canonical
  messages from explicitly trusted peer relay bots can retain source names,
  selected radio metadata and scoped replies/reactions. Other Matrix senders
  remain ordinary Matrix users.
- **More predictable runtime ownership.** Queue stop/restart, plugin scheduler
  reloads, radio connection shutdown and delayed subscriptions are protected
  against late callbacks and overlapping owners.
- **Better radio identity and connection handling.** Packet sender IDs are
  normalized consistently; stable serial device aliases and mapped character
  devices are supported. Weather requests no longer require the sender to
  already exist in NodeDB.
- **Updated release dependencies.** Python **3.12 or later** is required;
  the coordinated Mindroom-nio, mtjk and MEDRE package pins are recorded in
  the release manifest and lockfile.

For a step-by-step upgrade, including backups and rollback,
see [Upgrading from 1.4.1 to 1.5.0](UPGRADING_1.5.md).

## Changes requiring operator attention

### Python, SDKs and encrypted rooms

- **Python 3.11 is no longer supported.** MMRelay 1.4.1 is the last released
  version supporting it. The 1.5 native package requires Python 3.12 or later.
  For encrypted Matrix rooms on **macOS Python 3.15**, the locked vodozemac
  0.10.0 has no prebuilt macOS 3.15 wheel and may need a Rust source build.
  Prefer a Python version with an available wheel if native build tooling is
  not installed; see the [E2EE guide](E2EE.md#requirements-and-platform-support).
- MMRelay now uses a single `mindroom-nio` 1.x dependency path. Do not install
  `matrix-nio` into the same environment or manually replace the selected SDK.
- **Windows Matrix E2EE is not supported in 1.5.0.** Native Windows and the
  Windows installer can use unencrypted Matrix rooms and OAuth independently
  of E2EE. Windows E2EE needs further upstream `mindroom-nio` work and is not
  included in this release. Linux/macOS encrypted-room support remains.
- Preserve `matrix/credentials.json` and the **complete** `matrix/store/`
  directory with the same bot account/device. Don't delete stores or reset
  cross-signing merely to complete an upgrade.

### Trusted relay metadata is explicit

For shared rooms bridged by more than one relay, configure the **exact** MXIDs
of trusted peer bots in `matrix.trusted_relays`:

```yaml
matrix:
  trusted_relays:
    - "@peer-relay:example.org"
```

The default is an empty allowlist. No wildcards, case folding, room-power
shortcuts, or trust-on-first-use rules are accepted. A message containing the
reserved `medre` Matrix-content namespace is a **relay claim**, even when its
envelope is malformed or incomplete:

- **Unlisted Matrix sender:** drop the message before plugins or radio delivery.
  A WARNING names the observed MXID and `matrix.trusted_relays`. Verify the
  account is an actual peer relay **before** trusting it; this is equally
  consistent with a missing allowlist entry or a forgery attempt.
- **Allowlisted sender, invalid/incoherent envelope:** drop with a separate
  WARNING. Check peer versions and provenance rather than adding a bypass.
- **No `medre` namespace:** ordinary Matrix messages, including human messages,
  retain normal behavior. A pre-1.5 bot that sends no canonical envelope cannot
  be identified as a peer by this gate. Old flat `meshtastic_*` keys remain
  inert and never grant provenance or override the visible authored body.

Warnings repeat at most once per sender and failure reason every five minutes
(per running process); repeated drops are recorded at DEBUG. Rejected messages
are _always_ dropped. The logs do not expose message bodies or raw envelopes. Authenticated Matrix sender identity
does not authenticate the claimed radio-origin identity.

**Avoid radio echo loops:** Do not connect multiple relays to the same radio
mesh and Matrix room until the topology has a separately qualified
loop-prevention plan. Dropping untrusted MEDRE claims prevents that traffic
from reaching the radio, but it cannot recognize non-envelope traffic from an
older relay, or protect against all misconfigured trusted peers. The allowlist
is **not** a general-purpose same-mesh loop guarantee.

### Source attribution, replies and reactions

- The canonical visible body is authoritative. Long/short radio names use
  generic sender attribution; selected portnum and meshnet labels use bounded
  `source.native_facts`. These are **adapter-reported claims**, not identity
  proof or authorization to relay a packet.
- Reply and reaction references retain adapter, room/channel and packet scope;
  radio packet ID **0** is valid. Reactions require an **explicit, nonblank,
  exact key**. Untargeted emotes remain ordinary authored messages.
- Where available, the canonical envelope carries the native Meshtastic node
  ID. Node IDs, including those derived from public keys on newer firmware,
  can remain stable and **link activity across Matrix rooms and radio meshes**.
  Treat them as privacy-relevant even when they are also broadcast over radio.
- Peer bot metadata does not authenticate the original radio sender or prove
  delivery. MMRelay still owns its Matrix authentication/E2EE, Meshtastic
  connection, plugins, SQLite message mappings and retries.

For implementers, see the [MEDRE Matrix event contract](dev/MEDRE_MATRIX_1_5_INTEGRATION.md).

## Additional improvements since 1.4.1

- OAuth session renewal and device-signature handling preserve the active
  Matrix session more carefully (the native OAuth flow remains experimental).
- Serial configuration accepts stable Linux `/dev/serial/by-id/` and
  `/dev/serial/by-path/` aliases, including explicitly mapped container
  character devices.
- Radio sender-node normalization reduces mismatched identity lookups.
- Radio reconnects and shutdown, outbound queue ownership, and plugin lifecycle
  are more defensive against overlapping workers and late callbacks.
- Remote peer radio text is bounded **after** the sender prefix is added,
  avoiding oversized UTF-8 payloads.
- Node-name cache migration reports when no cache data could be carried over.

These are targeted fixes and safeguards, **not** guarantees of exactly-once
radio delivery, crash-persistent queues, or seamless recovery from every
uncertain send.

## Packages and deployment images

The release uses **published indexed** dependencies; the earlier
direct Git-source requirements for the MEDRE core and Matrix adapter are
gone. Both `pyproject.toml` and `uv.lock` name the coordinated versions. A
subsequent MEDRE release is not implicitly part of the qualified pair merely
because it exists upstream.

The Helm chart metadata, default image tag and Kustomize tag advance with
the matching release image. When upgrading, select the release tag explicitly
(or pin its verified digest) and retain existing Helm values. See the
[upgrade guide](UPGRADING_1.5.md) and [Helm guide](HELM.md).

## Qualification evidence

A reviewed November 11 three-mesh laboratory campaign reported six passing
normal-topology scenarios, including shared-room fanout, channel parity,
replies, untrusted claims and LXMF loopback. It does **not** certify overlapping
same-mesh relays, all native radio tapbacks, every LXMF/XMPP operation, live
E2EE device recovery or the final 1.5.0 distribution artifacts.

The released revision passed the full hosted CI matrix (Python 3.12–3.15),
the Windows **unencrypted** packaging and install lanes, and the shared-room
meshtasticd integration in both peer-trust modes. Do not interpret green
build/test workflows as proof of delivery through physical radio hardware.
