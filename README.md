# MMRelay

**Meshtastic ↔ Matrix Relay**

MMRelay is a self-hosted bridge between a Meshtastic meshnet and Matrix. It runs
as a long-lived service, connects to one Meshtastic node, logs in to Matrix with
a bot account, and relays eligible traffic between configured Meshtastic
channels and Matrix rooms.

`Meshtastic meshnet ⇄ Meshtastic node ⇄ MMRelay ⇄ Matrix rooms`

## How it works at a glance

- **Meshtastic side:** MMRelay connects to a Meshtastic node over **Serial**, **BLE**, or **TCP/network** (for example, a node reachable over Wi-Fi).
- **Matrix side:** MMRelay joins configured rooms using a dedicated Matrix bot account.
- **Room/channel mapping:** each Meshtastic channel (0–7) is mapped 1:1 to a Matrix room.
- **Bidirectional relay:** eligible mesh traffic can be posted to the mapped Matrix room, and eligible Matrix traffic can be transmitted on the mapped Meshtastic channel.
- **Identity boundary:** messages posted into Matrix are sent by the MMRelay bot. Meshtastic sender names and node IDs are preserved as attribution; they are not Matrix-authenticated identities.

For the full conceptual model—including meshnet terminology, the recommended
relay-node setup, message flow, mapping behavior, and the identity/trust
boundary—see **[How MMRelay Works](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/How-MMRelay-Works)**.

## Start here

- **[Installation and setup](docs/INSTRUCTIONS.md)** — install MMRelay and create a configuration
- **[Getting started with Matrix](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Getting-Started-With-Matrix-&-MM-Relay)** — Matrix basics and account setup
- **[E2EE setup](docs/E2EE.md)** — encrypted Matrix rooms and bot-device identity
- **[Docker](docs/DOCKER.md)** · **[Helm](docs/HELM.md)** · **[Kubernetes](docs/KUBERNETES.md)** — deployment options
- **[What's new in 1.4](docs/WHATS_NEW_1.4.md)** — release and upgrade guidance
- **[Documentation index](docs/README.md)** — all versioned project documentation

## Highlights

- Bidirectional Meshtastic ↔ Matrix message relay
- Multiple meshnets and configurable room/channel mappings
- Serial, BLE, and TCP/network Meshtastic connections
- Matrix end-to-end encryption support
- Replies and reactions across the bridge
- Message formatting and payload-size handling for Meshtastic
- SQLite-backed node/message state
- Docker and Kubernetes deployment support
- Core, community, and local plugin support
- Optional MQTT integration through Meshtastic firmware

> **E2EE:** MMRelay uses [mindroom-nio](https://github.com/mindroom-ai/mindroom-nio)
> with vodozemac for encrypted Matrix rooms. Cross-signing applies to the Matrix
> bot device only; it does not authenticate Meshtastic identities. See the
> [E2EE guide](docs/E2EE.md), especially before resetting authentication or
> cross-signing state.
>
> **Meshtastic library:** MMRelay uses [mtjk](https://github.com/jeremiah-k/mtjk),
> a Meshtastic Python fork with additional BLE, connection-lifecycle, and
> thread-safety work used by this project.

## Plugins

MMRelay can be extended with built-in core plugins, Git-managed community
plugins, and local custom plugins. Plugins run inside the MMRelay process, so
install third-party code only from sources you trust.

- [Core Plugins](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Core-Plugins)
- [Community Plugin List](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Community-Plugin-List)
- [Plugin Development Guide](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Plugin-Development-Guide)

## Community

- Project room: [#mmrelay:matrix.org](https://matrix.to/#/#mmrelay:matrix.org)
- Meshnet Club space: [#meshnetclub:matrix.org](https://matrix.to/#/#meshnetclub:matrix.org)
- Public relay room: [#mmrelay-relay-room:matrix.org](https://matrix.to/#/#mmrelay-relay-room:matrix.org)
