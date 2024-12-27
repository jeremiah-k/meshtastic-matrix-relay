# M<>M Relay

## (Meshtastic <=> Matrix Relay)

A powerful and easy-to-use relay between Meshtastic devices and Matrix chat rooms, allowing seamless communication across platforms. This opens the door for bridging Meshtastic devices to [many other platforms](https://matrix.org/bridges/).

### Features

- Bidirectional message relay between Meshtastic devices and Matrix chat rooms, capable of supporting multiple meshnets
- Supports serial, network, and **_BLE (now too!)_** connections for Meshtastic devices
- Custom keys are embedded in Matrix messages which are used when relaying messages between two or more meshnets.
- Truncates long messages to fit within Meshtastic's payload size
- SQLite database to store node information for improved functionality
- Customizable logging level for easy debugging
- Configurable through a simple YAML file
- Supports mapping multiple rooms and channels 1:1
- Relays messages to/from an MQTT broker, if configured in the Meshtastic firmware
- ✨️ _Cross-platform reactions support_ ✨️ **NEW!!**

_We would love to support [Matrix E2EE rooms](https://github.com/geoffwhittington/meshtastic-matrix-relay/issues/33), but this is currently not implemented._

### Windows Installer

![Windows Installer Screenshot](https://user-images.githubusercontent.com/1770544/235249050-8c79107a-50cc-4803-b989-39e58100342d.png)

The latest installer is available [here](https://github.com/geoffwhittington/meshtastic-matrix-relay/releases)

### Plugins

M<>M Relay supports plugins for extending its functionality, enabling customization and enhancement of the relay to suit specific needs.

## Core Plugins

Generate a map of your nodes:

![Map Plugin Screenshot](https://user-images.githubusercontent.com/1770544/235247915-47750b4f-d505-4792-a458-54a5f24c1523.png)

Produce high-level details about your mesh:

![Mesh Details Screenshot](https://user-images.githubusercontent.com/1770544/235245873-1ddc773b-a4cd-4c67-b0a5-b55a29504b73.png)

See the full list of core plugins [here](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki/Core-Plugins).

## Community & custom plugins

It is possible to create custom plugins and to also easily share them with the community. Check [example_plugins/README.md](https://github.com/geoffwhittington/meshtastic-matrix-relay/tree/main/example_plugins) and also the [Community Plugins Development Guide](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki/Community-Plugin-Development-Guide).

✨️ Visit our recently added [Community Plugins List](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki/Community-Plugin-List)!

### Install a community plugin

Add the repository under the `community-plugins` section in `config.yaml`:

```yaml
community-plugins:
  example-plugin:
    active: true
    repository: https://github.com/jeremiah-k/mmr-plugin-template.git
    tag: main
```

### Getting Started with Matrix

See our Wiki page [Getting Started With Matrix & MM Relay](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki/Getting-Started-With-Matrix-&-MM-Relay).

### Already on Matrix?

Join us!

- Our project's room: [#mmrelay:meshnet.club](https://matrix.to/#/#mmrelay:meshnet.club)
- Part of the Meshtastic Community Matrix space: [#meshtastic-community:meshnet.club](https://matrix.to/#/#meshtastic-community:meshnet.club)

### Supported Platforms

The relay is compatible with the following operating systems:

- Linux
- MacOS
- Windows

Refer to [the development instructions](DEVELOPMENT.md) for details about running the relay on MacOS and Linux.
