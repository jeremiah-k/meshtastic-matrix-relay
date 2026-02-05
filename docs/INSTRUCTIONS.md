# MMRelay Instructions

MMRelay works on Linux, macOS, and Windows and requires Python 3.10+.

## Installation

### Quick Install (Recommended)

```bash
# Install using pipx for isolated installation (recommended)
pipx install mmrelay

# Pip will also work if you prefer
pip install mmrelay
```

For pipx installation instructions, see: [pipx installation guide](https://pipx.pypa.io/stable/installation/#on-linux)

### Developer Install

If you want to contribute or modify the code:

```bash
# Clone the repository
git clone https://github.com/jeremiah-k/meshtastic-matrix-relay.git
cd meshtastic-matrix-relay

# Install in development mode using pipx (recommended)
pipx install -e .

# For E2EE development (Linux/macOS only)
pipx install -e '.[e2e]'

# Uninstall before testing new changes
pipx uninstall mmrelay
```

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
mmrelay config generate

# Edit the generated configuration file with your preferred editor
nano ~/.mmrelay/config.yaml
```

This command will:

1. Check if a configuration file already exists (to avoid overwriting it)
2. Create the necessary directory structure if it doesn't exist
3. Generate a sample configuration file at `~/.mmrelay/config.yaml`

### Matrix Authentication Setup

**Standard Method (v1.2+)**: Use the built-in authentication command:

```bash
mmrelay auth login
```

This interactive command will:

- Prompt for your Matrix homeserver, username, and password
- Create secure credentials and save to `~/.mmrelay/credentials.json`
- Set up encryption keys for secure communication (Linux/macOS)
- Works for regular Matrix communication on all platforms
- **Use modern OIDC authentication** compatible with Matrix 2.0 and MAS (Matrix Authentication Service)

**Why use `mmrelay auth login`?**

- **Future-proof**: Compatible with Matrix Authentication Service (MAS) used by matrix.org and other modern homeservers.
- **Token rotation**: Automatically handles token refresh, preventing authentication expiration.
- **Required for E2EE**: Essential for encrypted room support.
- **Secure**: Uses proper OIDC flows instead of long-lived access tokens.

**Platform Notes**:

- **Linux/macOS**: Full E2EE support with automatic encryption
- **Windows**: Regular Matrix communication (E2EE not available due to library limitations)

### Configuration Tips

- Review the comments in the sample configuration file for detailed explanations
- **Use `mmrelay auth login` for Matrix authentication** (required for E2EE)
- Configure your Meshtastic connection details in the config file
- For advanced setups, check the plugin configuration options
- For advanced features like message prefix customization, debug logging, and environment variable overrides, see the [Advanced Configuration Guide](ADVANCED_CONFIGURATION.md)
- For E2EE setup and troubleshooting, see the [E2EE Guide](E2EE.md)

## Running MMRelay

### Basic Usage

Start the relay with a single command:

```bash
mmrelay
```

By default, MMRelay uses `~/.mmrelay` as the home directory for all runtime data on Linux/macOS. On Windows, it uses the platform-specific application data directory (e.g., `%APPDATA%/mmrelay`).

### Command-Line Options

Customize your setup with command-line options:

```bash
mmrelay --config /path/to/config.yaml --home /path/to/mmrelay-home
```

**Common flags:**

- `--config PATH` - Specify a custom configuration file location
- `--home PATH` - Set the home directory for all runtime data (credentials, logs, database, plugins)
- `--log-level {error,warning,info,debug}` - Set the logging verbosity
- `--version` - Show version information and exit
- `--help` - Display help message

### Data Locations

MMRelay stores all runtime data in the home directory (`~/.mmrelay` by default, or as specified with `--home` or the `MMRELAY_HOME` environment variable). This directory contains:

- `config.yaml` - Your configuration file
- `credentials.json` - Matrix authentication credentials (created by `mmrelay auth login`)
- `database/meshtastic.sqlite` - SQLite database for node information
- `logs/` - Application logs
- `store/` - E2EE encryption keys (Linux/macOS)
- `plugins/` - Custom and community plugins

### Useful Commands

```bash
# Generate a sample configuration file
mmrelay config generate

# Validate your configuration
mmrelay config check

# Install as a systemd user service (Linux only)
mmrelay service install

# Set up Matrix E2EE authentication (for encrypted rooms)
mmrelay auth login
```

### Migrating from an Older Setup?

If you're upgrading from a previous version of MMRelay with the old directory layout, see the [Migration Guide for v1.3](MIGRATION_1.3.md) for detailed instructions on migrating to the unified HOME model.

## Running as a Service

### Systemd Service (Linux)

For automatic startup and management on Linux systems, MMRelay includes a built-in command to set up a systemd user service:

```bash
mmrelay service install
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

## Docker

MMRelay includes official Docker support for easy deployment and management. Docker provides isolated environment, automatic dependency management, easy updates, and consistent deployment across different systems.

### Quick Docker Setup

```bash
# Clone the repository (if you haven't already)
git clone https://github.com/jeremiah-k/meshtastic-matrix-relay.git
cd meshtastic-matrix-relay

# Set up configuration and start
make setup    # Copy config and open editor (first time)
make build    # Build the Docker image
make run      # Start the container
make logs     # View logs
```

For detailed Docker commands, configuration options, connection types, and troubleshooting, see the [Docker Guide](DOCKER.md).

## Kubernetes

MMRelay ships static Kubernetes manifests in `deploy/k8s/`. Copy them into your deployment repo, create a Secret that contains your `config.yaml`, then apply with `kubectl`.

Optional: use the digest overlay in `deploy/k8s/overlays/digest/` to pin a container image by SHA.

For detailed Kubernetes deployment instructions, see the [Kubernetes Guide](KUBERNETES.md).

## Development

### Contributing

Contributions are welcome! We use **Trunk** for automated code quality checks and formatting. The `trunk` launcher is committed directly to the repo, please run checks before submitting pull requests.

```bash
.trunk/trunk check --all --fix
```
