# What's new in MMRelay 1.5.0

> **Release candidate — not a release announcement.** These notes are staged on
> `docs/release-1.5`. Do not upgrade until the 1.5.0 package, container image,
> and corresponding release tag are published. For the current 1.4.1 release,
> use the [1.4.1 documentation](https://github.com/jeremiah-k/meshtastic-matrix-relay/tree/1.4.1/docs).

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
  `mindroom-nio==1.4.2`, `mtjk==2.7.11.post14`, `medre==0.2.0` and
  `medre-adapter-matrix==0.2.0` are pinned in the candidate manifest and lock.

For a step-by-step upgrade, including backups and rollback,
see [Upgrading from 1.4.1 to 1.5.0](UPGRADING_1.5.md).

## Changes requiring operator attention

### Python, SDKs and encrypted rooms

- **Python 3.11 is no longer supported.** MMRelay 1.4.1 is the last released
  version supporting it. Use Python 3.12–3.15 for a 1.5 native installation.
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
shortcuts, or trust-on-first-use rules are accepted. Claims from other senders
cannot replace observed Matrix identity, local reply mappings, or the authored
visible message body. Old flat `meshtastic_*` message-content fields, including
`meshtastic_text` overrides, are deliberately **not** interpreted as peer
metadata in 1.5; mixed 1.4/1.5 peers do not have metadata compatibility.

**Avoid radio echo loops:** Do not connect multiple relays to the same radio
mesh and Matrix room until the topology has a separately qualified
loop-prevention plan. Trusted bot configuration is necessary for interpreting
peer metadata but is **not** a general-purpose same-mesh loop guarantee.
An untrusted or pre-1.5 relay is treated like an ordinary Matrix sender,
even if it claims to originate on the local meshnet.

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

The candidate source uses **published indexed** dependencies, including
`medre==0.2.0` and `medre-adapter-matrix==0.2.0`; earlier direct Git-source
requirements are gone. Both `pyproject.toml` and `uv.lock` name the coordinated
versions. A subsequent MEDRE release is not implicitly part of the qualified
pair merely because it exists upstream.

The Helm chart metadata, default image tag and Kustomize tag remain on
**1.4.1** while the 1.5.0 image is unpublished. When it is published, select
1.5.0 explicitly (or pin its verified digest), retain existing Helm values,
and only promote deployment defaults alongside the corresponding artifacts.
See the [upgrade guide](UPGRADING_1.5.md) and [Helm guide](HELM.md).

## Evidence and release gates

A reviewed November 11 three-mesh laboratory campaign reported six passing
normal-topology scenarios, including shared-room fanout, channel parity,
replies, untrusted claims and LXMF loopback. It does **not** certify overlapping
same-mesh relays, all native radio tapbacks, every LXMF/XMPP operation, live
E2EE device recovery or the final 1.5.0 distribution artifacts.

Before publishing, qualify the exact indexed dependency pair and complete the
Python 3.12–3.15 CI matrix, Windows **unencrypted** install, supported
Linux/macOS E2EE recovery, multi-mesh/channel reply and reaction paths, radio
reconnects, plugin lifecycle, clean package installs and backup/rollback
rehearsal. Do not interpret green build/test workflows as proof of delivery
through physical radio hardware.
