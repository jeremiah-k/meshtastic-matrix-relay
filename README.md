# M<>M Relay

## (Meshtastic <=> Matrix Relay)

A powerful and easy-to-use relay between Meshtastic devices and Matrix chat rooms, allowing seamless communication across platforms. This opens the door for bridging Meshtastic devices to [many other platforms](https://matrix.org/bridges/).

## Documentation

Visit our [Wiki](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki) for comprehensive guides and information.

- [Installation Instructions](docs/INSTRUCTIONS.md) - Setup and configuration guide

---

## Quick Start

MMRelay runs on Linux, macOS, and Windows.

```bash
# Install using pipx for isolated installation (recommended)
pipx install mmrelay

# Generate a sample configuration file & then edit it
mmrelay --generate-config

# Start the relay (without --install-service to run manually)
mmrelay --install-service
```

For detailed installation and configuration instructions, see the [Installation Guide](docs/INSTRUCTIONS.md).

## Docker

MMRelay includes official Docker support for easy deployment and management:

```bash
# Quick setup with Docker
make config  # Creates config files and directories
make build   # Build the Docker image
make run     # Start the container

# View logs
make logs

# Stop the container
make stop
```

Docker provides:

- **Isolated environment** - No dependency conflicts
- **Easy deployment** - Single command setup
- **Automatic restarts** - Container restarts on failure
- **Volume persistence** - Data and logs preserved across restarts

For detailed Docker setup instructions, see the [Docker Guide](DOCKER.md) or [Installation Guide](docs/INSTRUCTIONS.md#docker).

---

## Features

- Bidirectional message relay between Meshtastic devices and Matrix chat rooms, capable of supporting multiple meshnets
- Supports serial, network, and **_BLE (now too!)_** connections for Meshtastic devices
- Custom fields are embedded in Matrix messages for relaying messages between multiple meshnets
- Truncates long messages to fit within Meshtastic's payload size
- SQLite database to store node information for improved functionality
- Customizable logging level for easy debugging
- Configurable through a simple YAML file
- Supports mapping multiple rooms and channels 1:1
- Relays messages to/from an MQTT broker, if configured in the Meshtastic firmware
- ✨️ _Bidirectional replies and reactions support_ ✨️ **NEW!!**

_We would love to support [Matrix E2EE rooms](https://github.com/geoffwhittington/meshtastic-matrix-relay/issues/33), but this is currently not implemented._

---

## Windows Installer

![Windows Installer Screenshot](https://user-images.githubusercontent.com/1770544/235249050-8c79107a-50cc-4803-b989-39e58100342d.png)

The latest installer is available in the [releases section](https://github.com/geoffwhittington/meshtastic-matrix-relay/releases).

---

## Plugins

M<>M Relay supports plugins for extending its functionality, enabling customization and enhancement of the relay to suit specific needs.

### Core Plugins

Generate a map of your nodes:

![Map Plugin Screenshot](https://user-images.githubusercontent.com/1770544/235247915-47750b4f-d505-4792-a458-54a5f24c1523.png)

Produce high-level details about your mesh:

![Mesh Details Screenshot](https://user-images.githubusercontent.com/1770544/235245873-1ddc773b-a4cd-4c67-b0a5-b55a29504b73.png)

See the full list of [core plugins](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki/Core-Plugins).

### Community & Custom Plugins

MMRelay's plugin system allows you to extend functionality in two ways:

- **Custom Plugins**: Create personal plugins for your own use, stored in `~/.mmrelay/plugins/custom/`
- **Community Plugins**: Share your creations with others or use plugins developed by the community

Check the [Community Plugins Development Guide](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki/Community-Plugin-Development-Guide) in our wiki to get started.

✨️ Visit the [Community Plugins List](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki/Community-Plugin-List)!

#### Install a Community Plugin

Add the repository under the `community-plugins` section in `config.yaml`:

```yaml
community-plugins:
  example-plugin:
    active: true
    repository: https://github.com/jeremiah-k/mmr-plugin-template.git
    tag: main
```

### Plugin System

Plugins make it easy to extend functionality without modifying the core program. MMRelay features a powerful plugin system with standardized locations:

- **Core Plugins**: Pre-installed with the package
- **Custom Plugins**: Your own plugins in `~/.mmrelay/plugins/custom/`
- **Community Plugins**: Third-party plugins in `~/.mmrelay/plugins/community/`

---

## Getting Started with Matrix

See our Wiki page [Getting Started With Matrix & MM Relay](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki/Getting-Started-With-Matrix-&-MM-Relay).

---

## Already on Matrix?

Join us!

- Our project's room: [#mmrelay:meshnet.club](https://matrix.to/#/#mmrelay:meshnet.club)
- Part of the Meshtastic Community Matrix space: [#meshtastic-community:meshnet.club](https://matrix.to/#/#meshtastic-community:meshnet.club)
- Public Relay Room: [#relay-room:meshnet.club](https://matrix.to/#/#relay-room:meshnet.club) - Where we bridge multiple meshnets. Feel free to join us, with or without a relay!
