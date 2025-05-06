# MMRelay Instructions

MMRelay works on Linux, macOS, and Windows and requires Python 3.9+.

## Installation

### Quick Install (Recommended)

```bash
# Install using pipx for isolated installation (recommended)
pipx install mmrelay

# Pip will also work if you prefer
pip install mmrelay
```

For pipx installation instructions, see: [pipx installation guide](https://pipx.pypa.io/stable/installation/#on-linux)

### Standalone Executable for ARMv7

For ARMv7 devices (like Raspberry Pi), we provide a standalone `.pyz` executable:

```bash
# Download the latest release
wget https://github.com/geoffwhittington/meshtastic-matrix-relay/releases/download/v1.0.6/mmrelay-1.0.6-armv7.pyz

# Make it executable
chmod +x mmrelay-1.0.6-armv7.pyz

# Run it
./mmrelay-1.0.6-armv7.pyz
```

For more details, see [PYZ_BUILDS.md](docs/PYZ_BUILDS.md).

### Developer Install

If you want to contribute or modify the code:

```bash
# Clone the repository
git clone https://github.com/geoffwhittington/meshtastic-matrix-relay.git
cd meshtastic-matrix-relay

# Install in development mode using pipx (recommended)
# Run this command each time before testing new changes.
pipx install -e .
```

> **Upgrading from a previous version?** Please see [UPGRADE_TO_V1.md](UPGRADE_TO_V1.md) for migration guidance.

## Configuration

### Configuration File Locations

MMRelay looks for configuration files in the following locations (in order):

1. Path specified with `--config` command-line option
2. `~/.mmrelay/config.yaml` (recommended location)
3. Current directory `config.yaml` (for backward compatibility)

### Setting Up Your Configuration

MMRelay includes a built-in command to generate a sample configuration file in the recommended location:

```bash
# Generate a sample configuration file
mmrelay --generate-config

# Edit the generated configuration file with your preferred editor
nano ~/.mmrelay/config.yaml
```

This command will:

1. Check if a configuration file already exists (to avoid overwriting it)
2. Create the necessary directory structure if it doesn't exist
3. Generate a sample configuration file at `~/.mmrelay/config.yaml`

### Configuration Tips

- Review the comments in the sample configuration file for detailed explanations
- At minimum, you'll need to configure your Matrix credentials and Meshtastic connection
- For advanced setups, check the plugin configuration options

## Running MMRelay

### Basic Usage

Start the relay with a single command:

```bash
mmrelay
```

### Command-Line Options

Customize your setup with command-line options:

```bash
mmrelay --config /path/to/config.yaml --logfile /path/to/logfile.log
```

```bash
mmrelay [OPTIONS]

Options:
  -h, --help            Show this help message and exit
  --config PATH         Path to the configuration file
  --data-dir PATH       Base directory for all data (logs, database, plugins)
  --log-level {error,warning,info,debug}
                        Set logging level
  --logfile PATH        Path to log file (can be overridden by --data-dir)
  --version             Show version and exit
  --generate-config     Generate a sample config.yaml file
  --check-config        Check if the configuration file is valid
  --install-service     Install or update the systemd user service
```

#### Useful Commands

```bash
# Generate a sample configuration file
mmrelay --generate-config

# Validate your configuration
mmrelay --check-config

# Install as a systemd user service (Linux only)
mmrelay --install-service
```

### What to Expect

When running successfully, you'll see output similar to this:

```text
INFO: Loading configuration from: /home/user/.mmrelay/config.yaml
INFO: Starting Meshtastic <==> Matrix Relay...
INFO: Connecting to radio at meshtastic.local ...
INFO: Connected to radio at meshtastic.local
INFO: Listening for inbound radio messages ...
INFO: Listening for inbound matrix messages ...
```

Messages will be relayed in both directions automatically:

## Running as a Service

### Systemd Service (Linux)

For automatic startup and management on Linux systems, MMRelay includes a built-in command to set up a systemd user service:

```bash
mmrelay --install-service
```

This command will:

1. Create the necessary directories (service file location and log directory)
2. Install or update the systemd user service file
3. Reload the systemd daemon
4. Check if your configuration is valid
5. Ask if you want to enable the service to start at boot
6. Ask if you want to start the service immediately
7. Show the service status if started
8. Display commands for controlling the service

### Managing the Service

After installation, you can control the service with these commands:

```bash
# Start the service
systemctl --user start mmrelay.service

# Stop the service
systemctl --user stop mmrelay.service

# Restart the service
systemctl --user restart mmrelay.service

# Check service status
systemctl --user status mmrelay.service

# View service logs
journalctl --user -u mmrelay.service

# Or watch the application log file in real-time
tail -f ~/.mmrelay/logs/mmrelay.log
```

## Dockerized Versions (Unofficial)

If you would prefer to use a Dockerized version of the relay, there are unofficial third-party projects available. Please note that these are not officially supported, and issues should be reported to their respective repositories.

> **Note**: Dockerized versions may need updates to be compatible with MMRelay v1.0. Check with the maintainers of these projects for their status.

For more details, visit the [Third Party Projects](https://github.com/geoffwhittington/meshtastic-matrix-relay/wiki/Third-Party-Projects) page.

## Development

### Contributing

Contributions are welcome! We use **Trunk** for automated code quality checks and formatting. The `trunk` launcher is committed directly to the repo, please run checks before submitting pull requests.

```bash
.trunk/trunk check --all --fix
```
