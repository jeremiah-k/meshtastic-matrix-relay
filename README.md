# M<>M Relay

## ✨ Version 1.0 Released! ✨

**We're excited to announce MMRelay v1.0 with improved packaging, standardized directories, and enhanced CLI!**

**Existing users:** Version 1.0 requires a few quick migration steps:

1. Follow the [UPGRADE_TO_V1.md](UPGRADE_TO_V1.md) guide for a smooth transition
2. Move your configuration to the new standard location (`~/.mmrelay/config.yaml`)
3. See [ANNOUNCEMENT.md](ANNOUNCEMENT.md) for all the exciting new features

Not ready to upgrade yet? No problem! Run `git checkout 0.10.1` to continue using the previous version.

## (Meshtastic <=> Matrix Relay)

A powerful and easy-to-use relay between Meshtastic devices and Matrix chat rooms, allowing seamless communication across platforms. This opens the door for bridging Meshtastic devices to [many other platforms](https://matrix.org/bridges/).

---

## Getting Started

MMRelay runs on Linux, macOS, and Windows.

### Quick Installation

```bash
# Install using pipx for isolated installation (recommended)
pipx install mmrelay

# Pip will also work if you prefer
pip install mmrelay
```

For pipx installation instructions, see: [pipx installation guide](https://pipx.pypa.io/stable/installation/#on-linux)

### Standalone Executable (ARMv7)

For ARMv7 devices (like Raspberry Pi), we provide a standalone `.pyz` executable:

1. Download the latest `mmrelay-x.x.x-armv7.pyz` from [Releases](https://github.com/geoffwhittington/meshtastic-matrix-relay/releases)
2. Make it executable: `chmod +x mmrelay-x.x.x-armv7.pyz`
3. Run it: `./mmrelay-x.x.x-armv7.pyz`

This standalone version includes all dependencies with native extensions.

### Resources

- **New Users**: See [INSTRUCTIONS.md](INSTRUCTIONS.md) for setup and configuration
- **Existing Users**: See [UPGRADE_TO_V1.md](UPGRADE_TO_V1.md) for migration guidance
- **Configuration**: Review [sample_config.yaml](src/mmrelay/tools/sample_config.yaml) for examples

### Command-Line Options

```bash
usage: mmrelay [-h] [--config CONFIG] [--data-dir DATA_DIR] [--log-level {error,warning,info,debug}] [--logfile LOGFILE] [--version] [--generate-config] [--install-service] [--check-config]

Options:
  -h, --help            Show this help message and exit
  --config CONFIG       Path to config file
  --data-dir DATA_DIR   Base directory for all data (logs, database, plugins)
  --log-level {error,warning,info,debug}
                        Set logging level
  --logfile LOGFILE     Path to log file (can be overridden by --data-dir)
  --version             Show version and exit
  --generate-config     Generate a sample config.yaml file
  --install-service     Install or update the systemd user service
  --check-config        Check if the configuration file is valid
```

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
- ✨️ _Cross-platform reactions support_ ✨️ **NEW!!**

_We would love to support [Matrix E2EE rooms](https://github.com/geoffwhittington/meshtastic-matrix-relay/issues/33), but this is currently not implemented._

---

## Windows Installer

![Windows Installer Screenshot](https://user-images.githubusercontent.com/1770544/235249050-8c79107a-50cc-4803-b989-39e58100342d.png)

The latest installer is available [here](https://github.com/geoffwhittington/meshtastic-matrix-relay/releases).

---

## Plugins

M<>M Relay supports plugins for extending its functionality, enabling customization and enhancement of the relay to suit specific needs.

### Core Plugins

Generate a map of your nodes:

![Map Plugin Screenshot](https://user-images.githubusercontent.com/1770544/235247915-47750b4f-d505-4792-a458-54a5f24c1523.png)

Produce high-level details about your mesh:

![Mesh Details Screenshot](https://user-images.githubusercontent.com/1770544/235245873-1ddc773b-a4cd-4c67-b0a5-b55a29504b73.png)

See the full list of core plugins [here](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki/Core-Plugins).

### Community & Custom Plugins

It is possible to create custom plugins and share them with the community. Check [example_plugins/README.md](https://github.com/geoffwhittington/meshtastic-matrix-relay/tree/main/example_plugins) and the [Community Plugins Development Guide](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki/Community-Plugin-Development-Guide).

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

MMRelay features a powerful plugin system with standardized locations:

- **Core Plugins**: Pre-installed with the package
- **Custom Plugins**: Your own plugins in `~/.mmrelay/plugins/custom/`
- **Community Plugins**: Third-party plugins in `~/.mmrelay/plugins/community/`

Plugins make it easy to extend functionality without modifying the core code.

---

## Getting Started with Matrix

See our Wiki page [Getting Started With Matrix & MM Relay](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki/Getting-Started-With-Matrix-&-MM-Relay).

---

## Already on Matrix?

Join us!

- Our project's room: [#mmrelay:meshnet.club](https://matrix.to/#/#mmrelay:meshnet.club)
- Part of the Meshtastic Community Matrix space: [#meshtastic-community:meshnet.club](https://matrix.to/#/#meshtastic-community:meshnet.club)
- Public Relay Room: [#relay-room:meshnet.club](https://matrix.to/#/#relay-room:meshnet.club) - Where we bridge multiple meshnets. Feel free to join us, with or without a relay!
