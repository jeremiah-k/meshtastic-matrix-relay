# Docker Deployment

Simple Docker setup for Meshtastic Matrix Relay.

## Quick Start

**Option 1: One-step setup (recommended for first time):**

```bash
make setup    # Copies config, .env, and docker-compose.yaml, then opens editor
make build    # Build Docker image
make run      # Start container
```

**Option 2: Manual steps:**

```bash
make config   # Copy sample files (config.yaml, .env, docker-compose.yaml)
make edit     # Edit config with your preferred editor
make build    # Build Docker image
make run      # Start container
```

## Commands

- `make setup` - Copy sample config and open editor (recommended for first time)
- `make config` - Copy sample files and create directories (config.yaml, .env, docker-compose.yaml)
- `make edit` - Edit config file with your preferred editor
- `make build` - Build Docker image (uses layer caching for faster builds)
- `make build-nocache` - Build Docker image with --no-cache for fresh builds
- `make rebuild` - Stop, rebuild with --no-cache, and restart container (for updates)
- `make run` - Start container
- `make stop` - Stop container (keeps container for restart)
- `make logs` - Show container logs
- `make shell` - Access container shell
- `make clean` - Remove containers and networks

## Connection Types

**TCP (recommended):**

- Uses port mapping for cross-platform compatibility
- Set `meshtastic.host` in ~/.mmrelay/config.yaml
- Meshtastic typically uses port 4403 for TCP connections
- Container exposes port 4403 to host

**Serial:**

- Uncomment device mapping in docker-compose.yaml
- Set `meshtastic.serial_port` in ~/.mmrelay/config.yaml

**BLE:**

- Uncomment `privileged: true` in docker-compose.yaml
- Set `meshtastic.ble_address` in ~/.mmrelay/config.yaml

## Data Persistence

Uses the same directories as standalone installation:

- **Config**: `~/.mmrelay/config.yaml` (mounted read-only)
- **Database**: `~/.mmrelay/data/` (persistent)
- **Logs**: `~/.mmrelay/logs/` (persistent)

This means your Docker and standalone installations share the same data!

**Environment Configuration:**
Docker Compose uses the `.env` file to set data directory paths. The `make config` command creates this automatically with:

```bash
MMRELAY_HOME=$HOME
```

**Custom Data Location:**
To use a different location, edit the `.env` file:

```bash
MMRELAY_HOME=/path/to/your/data
```

## Updates

```bash
git pull
make rebuild    # Stop, rebuild with fresh code, and restart
```
