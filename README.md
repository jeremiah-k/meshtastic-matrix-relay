# MMRelay

## (Meshtastic <=> Matrix Relay)

A powerful and easy-to-use relay between Meshtastic devices and Matrix chat rooms, allowing seamless communication across platforms. This opens the door for bridging Meshtastic devices to [many other platforms](https://matrix.org/bridges/).

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
- ✨️ _Bidirectional replies and reactions support_ ✨️
- ✨️ _Native Docker support_ ✨️
- 🔐 **Matrix End-to-End Encryption (E2EE) support** 🔐 **NEW in v1.2!**

**MMRelay v1.2** introduces **Matrix End-to-End Encryption** support for secure communication in encrypted rooms. Messages are automatically encrypted/decrypted when communicating with encrypted Matrix rooms, with simple setup using `mmrelay auth login` or automatic credentials creation from config.yaml.

## Documentation

MMRelay supports multiple deployment methods including pipx, Docker, and Kubernetes. For complete setup instructions and all deployment options, see:

- [Installation Instructions](docs/INSTRUCTIONS.md) - Setup and configuration guide
- [Docker Guide](docs/DOCKER.md) - Docker deployment methods
- [Kubernetes Guide](docs/KUBERNETES.md) - Kubernetes deployment guide
- [What's New in v1.2](docs/WHATS_NEW_1.2.md) - New features and improvements
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

### Community & Custom Plugins

MMRelay's plugin system allows you to extend functionality in two ways:

- **Custom Plugins**: Create personal plugins for your own use, stored in `~/.mmrelay/plugins/custom/`
- **Community Plugins**: Share your creations with others or use plugins developed by the community

Check the [Community Plugins Development Guide](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Community-Plugin-Development-Guide) in our wiki to get started.

✨️ Visit the [Community Plugins List](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Community-Plugin-List)!

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

See our Wiki page [Getting Started With Matrix & MMRelay](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Getting-Started-With-Matrix-&-MM-Relay).

---

## Already on Matrix?

Join us!

- Our project's room: [#mmrelay:matrix.org](https://matrix.to/#/#mmrelay:matrix.org)
- Part of the Meshnet Club Matrix space: [#meshnetclub:matrix.org](https://matrix.to/#/#meshnetclub:matrix.org)
- Public Relay Room: [#mmrelay-relay-room:matrix.org](https://matrix.to/#/#mmrelay-relay-room:matrix.org) - Where we bridge multiple meshnets. Feel free to join us, with or without a relay!
