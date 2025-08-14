# Docker Deployment

MMRelay supports Docker deployment with two image options and multiple deployment methods.

## Table of Contents

- [Deployment Methods](#deployment-methods)
  - [Method A: Prebuilt Images (Recommended)](#method-a-prebuilt-images-recommended)
  - [Method B: Build from Source (Easy with Make Commands)](#method-b-build-from-source-easy-with-make-commands)
  - [Method C: Manual Setup / Portainer](#method-c-manual-setup--portainer)
- [Environment Variables](#environment-variables)
- [Make Commands Reference](#make-commands-reference)
- [Connection Types](#connection-types)
- [Data Persistence](#data-persistence)
- [Troubleshooting](#troubleshooting)
- [Updates](#updates)

## Deployment Methods

Choose the method that best fits your needs:

### Method A: Prebuilt Images (Recommended)

**Fast setup with official images** - no building required, perfect for most users.

- **Image**: `ghcr.io/jeremiah-k/mmrelay:latest`
- **Benefits**: Fastest setup, multi-platform (amd64/arm64), automatic updates
- **Best for**: Most users who want to run MMRelay quickly

**With make commands (if you have the repo):**

```bash
make setup-prebuilt  # Copy config, .env, and docker-compose.yaml, then opens editor
make run             # Start container (pulls official image)
```

### Method B: Build from Source (Easy with Make Commands)

**Full control with convenient tooling** - build your own image with simple commands.

- **Build**: Local compilation from source code
- **Benefits**: Full control, local modifications, development, latest features
- **Best for**: Developers, contributors, users who want customization

**With make commands:**

```bash
make setup    # Copy config, .env, and docker-compose.yaml, then opens editor
make build    # Build Docker image from source (convenient and fast)
make run      # Start container
```

### Method B: Manual Setup (Any Platform)

#### Step 1: Create directories

```bash
mkdir -p ~/.mmrelay/data ~/.mmrelay/logs
```

#### Step 2: Copy configuration files

```bash
# Copy sample config
cp src/mmrelay/tools/sample_config.yaml ~/.mmrelay/config.yaml

# Copy environment file
cp src/mmrelay/tools/sample.env .env

# Copy docker-compose file (choose one):
# For prebuilt images:
cp src/mmrelay/tools/sample-docker-compose-prebuilt.yaml docker-compose.yaml
# OR for building from source:
cp src/mmrelay/tools/sample-docker-compose.yaml docker-compose.yaml
```

#### Step 3: Edit configuration

```bash
# Edit the config file with your preferred editor
nano ~/.mmrelay/config.yaml
```

#### Step 4: Start container

```bash
# For prebuilt images:
docker compose up -d

# For building from source:
docker compose build
docker compose up -d
```

### Method C: Manual Setup / Portainer

#### Step 1: Prepare configuration

Create the MMRelay configuration directory on your host:

```bash
mkdir -p ~/.mmrelay/data ~/.mmrelay/logs
```

Download and edit the configuration file:

```bash
# Download sample config
curl -o ~/.mmrelay/config.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample_config.yaml

# Edit with your settings
nano ~/.mmrelay/config.yaml
```

#### Step 2: Create stack in Portainer

##### Option A: Use the official sample file (recommended)

Copy the latest docker-compose content from our official sample file:

- **View online**: [sample-docker-compose-prebuilt.yaml](https://github.com/jeremiah-k/meshtastic-matrix-relay/blob/main/src/mmrelay/tools/sample-docker-compose-prebuilt.yaml)
- **Download directly**:
  ```bash
  curl -o docker-compose.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample-docker-compose-prebuilt.yaml
  ```

##### Option B: Manual compose file

If you prefer to create your own, use this minimal configuration:

```yaml
services:
  mmrelay:
    image: ghcr.io/jeremiah-k/mmrelay:latest
    container_name: meshtastic-matrix-relay
    restart: unless-stopped
    user: "1000:1000" # May need to match your user's UID/GID. See the Troubleshooting section.
    environment:
      - TZ=UTC
      - PYTHONUNBUFFERED=1
      - MPLCONFIGDIR=/tmp/matplotlib
    volumes:
      # Replace /home/yourusername with your actual home directory
      - /home/yourusername/.mmrelay/config.yaml:/app/config.yaml:ro
      - /home/yourusername/.mmrelay/data:/app/data
      - /home/yourusername/.mmrelay/logs:/app/logs
    ports:
      - "4403:4403"
```

**Important for Portainer users:**

- Replace `/home/yourusername/.mmrelay/` with your actual home directory path
- For additional features (BLE, Watchtower), use the official sample file
- The official sample file is always up-to-date with the latest configuration options

## Environment Variables

The docker-compose files use environment variables for customization:

- **`MMRELAY_HOME`**: Base directory for MMRelay data (default: `$HOME`)
- **`UID`**: User ID for container permissions (default: `1000`)
- **`GID`**: Group ID for container permissions (default: `1000`)
- **`EDITOR`**: Preferred text editor for config editing (default: `nano`)

These are set in the `.env` file. For Portainer users, you can:

1. Set them in Portainer's environment variables section
2. Use absolute paths instead of variables in the docker-compose
3. Ensure the paths exist on your host system

## Make Commands Reference

### Setup Commands

- `make setup-prebuilt` - Copy config for prebuilt images and open editor (recommended)
- `make setup` - Copy config for building from source and open editor
- `make config` - Copy sample files and create directories (config.yaml, .env, docker-compose.yaml)
- `make edit` - Edit config file with your preferred editor

### Container Management

- `make run` - Start container (prebuilt images or built from source)
- `make stop` - Stop container (keeps container for restart)
- `make logs` - Show container logs
- `make shell` - Access container shell
- `make clean` - Remove containers and networks

### Build Commands (Source Only)

- `make build` - Build Docker image from source (uses layer caching for faster builds)
- `make build-nocache` - Build Docker image from source with --no-cache for fresh builds
- `make rebuild` - Stop, rebuild with --no-cache, and restart container (for updates)

### Manual Docker Commands

If not using make commands:

```bash
# Start with prebuilt image
docker compose up -d

# Build and start from source
docker compose build
docker compose up -d

# View logs
docker compose logs -f

# Stop containers
docker compose down

# Access shell
docker compose exec mmrelay bash
```

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

- Uncomment the BLE section in docker-compose.yaml (includes privileged mode, host networking, and D-Bus access)
- Set `meshtastic.ble_address` in ~/.mmrelay/config.yaml
- Note: BLE requires host networking mode which may affect port isolation

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

## Troubleshooting

### Common Portainer Issues

**Volume path errors:**

- Ensure paths like `/home/yourusername/.mmrelay/` exist on the host
- Replace `yourusername` with your actual username
- Create directories manually: `mkdir -p ~/.mmrelay/data ~/.mmrelay/logs`

**Permission errors:**

- Check that the user ID (1000) has access to the mounted directories
- Adjust `UID` and `GID` in environment variables if needed
- Use `chown -R 1000:1000 ~/.mmrelay/` to fix ownership

**Environment variable issues:**

- Portainer doesn't expand `$HOME` - use absolute paths
- Set environment variables in Portainer's stack environment section
- Or replace `${MMRELAY_HOME}` with absolute paths in the compose file

**Config file not found:**

- Verify the config file exists at the mounted path
- Check the volume mapping in the compose file
- Ensure the file is readable by the container user

### General Docker Issues

**Container won't start:**

- Check logs: `docker compose logs mmrelay`
- Verify config file syntax: `mmrelay check-config --config ~/.mmrelay/config.yaml`
- Ensure all required config fields are set

**Connection issues:**

- For TCP: Verify Meshtastic device IP and port 4403
- For Serial: Check device permissions and path
- For BLE: Ensure privileged mode and host networking are enabled

## Updates

**Prebuilt images:**

- Pull latest: `docker compose pull && docker compose up -d`
- Or use Watchtower for automatic updates (see sample-docker-compose-prebuilt.yaml)

**Built from source:**

```bash
git pull
make rebuild    # Stop, rebuild with fresh code, and restart
```
