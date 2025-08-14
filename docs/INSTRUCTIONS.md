# MMRelay Instructions

MMRelay v1.2+ works on Linux, macOS, and Windows and requires Python 3.9+.

**New in v1.2**: Full Matrix End-to-End Encryption (E2EE) support for secure communication in encrypted rooms (Linux/macOS only).

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
mmrelay generate-config

# Edit the generated configuration file with your preferred editor
nano ~/.mmrelay/config.yaml
```

This command will:

1. Check if a configuration file already exists (to avoid overwriting it)
2. Create the necessary directory structure if it doesn't exist
3. Generate a sample configuration file at `~/.mmrelay/config.yaml`

### Matrix Authentication Setup

**Recommended Method (v1.2+)**: Use the built-in authentication command:

```bash
mmrelay auth
```

This interactive command will:

- Prompt for your Matrix homeserver, username, and password
- Create secure credentials and save to `~/.mmrelay/credentials.json`
- Set up encryption keys for secure communication (Linux/macOS)
- Work for regular Matrix communication on all platforms

**Platform Notes**:

- **Linux/macOS**: Full E2EE support with automatic encryption
- **Windows**: Regular Matrix communication (E2EE not available due to library limitations)

**Alternative Method**: You can manually configure Matrix credentials in `config.yaml`:

```yaml
matrix:
  homeserver: https://your-matrix-server.org
  access_token: your_access_token
  bot_user_id: @yourbot:your-matrix-server.org
```

### Configuration Tips

- Review the comments in the sample configuration file for detailed explanations
- Use `mmrelay auth` for Matrix authentication (recommended)
- Configure your Meshtastic connection details in the config file
- For advanced setups, check the plugin configuration options
- For advanced features like message prefix customization and debug logging, see the [Extra Configuration Guide](EXTRA_CONFIGURATION.md)
- For E2EE setup and troubleshooting, see the [E2EE Guide](E2EE.md)

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
  --auth                Authenticate with Matrix and save credentials for E2EE support
```

#### Useful Commands

```bash
# Generate a sample configuration file
mmrelay generate-config

# Validate your configuration
mmrelay check-config

# Install as a systemd user service (Linux only)
mmrelay install-service

# Set up Matrix E2EE authentication (for encrypted rooms)
mmrelay auth
```

## Running as a Service

### Systemd Service (Linux)

For automatic startup and management on Linux systems, MMRelay includes a built-in command to set up a systemd user service:

```bash
mmrelay install-service
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

For detailed Docker commands, configuration options, connection types, and troubleshooting, see the [Docker Guide](../DOCKER.md).

## Development

### Contributing

Contributions are welcome! We use **Trunk** for automated code quality checks and formatting. The `trunk` launcher is committed directly to the repo, please run checks before submitting pull requests.

```bash
.trunk/trunk check --all --fix
```
