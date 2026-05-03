# MMRelay

## (Meshtastic <=> Matrix Relay)

A powerful and easy-to-use relay between Meshtastic devices and Matrix chat rooms, allowing seamless communication across platforms. This opens the door for bridging Meshtastic devices to [many other platforms](https://matrix.org/bridges/).

## Features

- Bidirectional message relay between Meshtastic devices and Matrix chat rooms, capable of supporting multiple meshnets
- Supports serial, network, and BLE connections for Meshtastic devices
- Custom fields are embedded in Matrix messages for relaying messages between multiple meshnets
- Truncates long messages to fit within Meshtastic's payload size
- SQLite database to store node information for improved functionality
- Customizable logging level for easy debugging
- Configurable through a simple YAML file
- Supports mapping multiple rooms and channels 1:1
- Relays messages to/from an MQTT broker, if configured in the Meshtastic firmware
- Bidirectional replies and reactions support
- Native Docker support
- Supports encrypted Matrix rooms 🔐 (Matrix E2EE)
- Unified directory structure 📁 (New in v1.3)

> **Encryption note (v1.3.7)**: MMRelay now uses **mindroom-nio** with **vodozemac** for Matrix E2EE. Most users, including Docker deployments and clean pipx/PyPI installs, should not need to do anything special. If you maintain a developer venv/editable install or an older in-place upgraded Python environment, verify that matrix-nio is not still installed alongside mindroom-nio. See the [E2EE Setup Guide](docs/E2EE.md) and the [v1.3 Migration Guide](docs/MIGRATION_1.3.md).
>
> **Improved BLE stability (v1.3.3)**: The Meshtastic Python library has been replaced with [mtjk](https://github.com/jeremiah-k/mtjk), a fork with BLE reliability improvements (auto-reconnection, state management, notification recovery) along with thread-safety and connection handling fixes. Changes may be upstreamed selectively once they've been battle-tested here. See the [Refactor Program](https://github.com/jeremiah-k/mtjk/blob/develop/REFACTOR_PROGRAM.md) for scope and rationale.

## Documentation

MMRelay supports multiple deployment methods including pip/pipx, Docker, and Kubernetes. For complete setup instructions and all deployment options, see:

- [Installation Instructions](docs/INSTRUCTIONS.md) - Setup and configuration guide
- [What's New in v1.3](docs/WHATS_NEW_1.3.md) - Latest release changes and migration info
- [Migration Guide for v1.3](docs/MIGRATION_1.3.md) - Upgrading from v1.2 or earlier
- [Docker Guide](docs/DOCKER.md) - Docker deployment methods
- [Kubernetes Guide](docs/KUBERNETES.md) - Kubernetes deployment guide
- [E2EE Setup Guide](docs/E2EE.md) - Matrix End-to-End Encryption configuration

---

## Plugins

MMRelay supports plugins for extending its functionality, enabling customization and enhancement of the relay to suit specific needs.

### Core Plugins

Generate a map of your nodes:

![Map Plugin Screenshot](https://user-images.githubusercontent.com/1770544/235247915-47750b4f-d505-4792-a458-54a5f24c1523.png)

Produce high-level details about your mesh:

![Mesh Details Screenshot](https://user-images.githubusercontent.com/1770544/235245873-1ddc773b-a4cd-4c67-b0a5-b55a29504b73.png)

See the full list of [core plugins](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Core-Plugins).

### Plugin System

MMRelay supports three plugin types:

- **Core Plugins**: Built in with MMRelay
- **Community Plugins**: Git-based plugins that MMRelay syncs for you
- **Custom Plugins**: Local/manual plugins for private use and development

MMRelay manages plugin directories under `MMRELAY_HOME` (default `~/.mmrelay`).
Most users only need `config.yaml`; path details matter mainly when authoring custom plugins.

Check the [Community Plugins Development Guide](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Community-Plugin-Development-Guide) in our wiki to get started.

✨️ Visit the [Community Plugins List](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Community-Plugin-List)!

### Install a Community Plugin

Add the repository under the `community-plugins` section in `config.yaml`:

```yaml
community-plugins:
  example-plugin:
    active: true
    repository: https://github.com/jeremiah-k/mmr-plugin-template.git
    commit: 0123456789abcdef0123456789abcdef01234567
    install_requirements: true
```

- Community plugins run in the same MMRelay process and inherit its permissions. Use trusted sources.
- Dependency installation is per-plugin and defaults to off (`install_requirements: false`).
- Prefer commit-pinned refs.
- Explicit `branch` and `tag` refs are allowed for dependency install, but MMRelay logs warnings.
- Missing ref (implicit default branch) is not eligible for dependency install.
- Dependencies install once per resolved local commit and are skipped when unchanged.

---

## Getting Started with Matrix

See our Wiki page [Getting Started With Matrix & MMRelay](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Getting-Started-With-Matrix-&-MM-Relay).

---

## Already on Matrix?

Join us!

- Our project's room: [#mmrelay:matrix.org](https://matrix.to/#/#mmrelay:matrix.org)
- Part of the Meshnet Club Matrix space: [#meshnetclub:matrix.org](https://matrix.to/#/#meshnetclub:matrix.org)
- Public Relay Room: [#mmrelay-relay-room:matrix.org](https://matrix.to/#/#mmrelay-relay-room:matrix.org) - Where we bridge multiple meshnets. Feel free to join us, with or without a relay!
