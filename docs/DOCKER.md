# Docker Deployment

MMRelay supports Docker deployment with two image options and multiple deployment methods.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start (Recommended)](#quick-start-recommended)
- [Deployment Methods](#deployment-methods)
  - [Prebuilt Images with Make](#prebuilt-images-with-make)
  - [Portainer/GUI Tools](#portainergui-tools)
  - [Build from Source with Make](#build-from-source-with-make)
  - [Build from Source without Make](#build-from-source-without-make)
- [Configuration](#configuration)
- [Matrix Authentication](#matrix-authentication)
- [Make Commands Reference](#make-commands-reference)
- [Connection Types](#connection-types)
- [Data Persistence](#data-persistence)
- [Switching Between Prebuilt and Source Build](#switching-between-prebuilt-and-source-build)
- [Troubleshooting](#troubleshooting)
- [Complete Docker Example](#complete-docker-example)
- [Updates](#updates)
- [Advanced Configuration](#advanced-configuration)

## Prerequisites

You need Docker installed on your system. Follow the [official Docker installation guide](https://docs.docker.com/engine/install/).

## Quick Start (Recommended)

**Most users should start here** - prebuilt images without cloning the repository:

> **Migrating?** If upgrading from an older version with the old directory layout, see the [Migration Guide for v1.3](MIGRATION_1.3.md).

```bash
# Create directory and download config
mkdir -p ~/.mmrelay
curl -Lo ~/.mmrelay/config.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample_config.yaml

# Adjust permissions and edit the file
chmod 600 ~/.mmrelay/config.yaml
nano ~/.mmrelay/config.yaml

# Set up environment and get docker-compose file
grep -q '^MMRELAY_HOST_HOME=' .env 2>/dev/null || echo "MMRELAY_HOST_HOME=${HOME}" >> .env
grep -q '^UID=' .env 2>/dev/null || echo "UID=$(id -u)" >> .env
grep -q '^GID=' .env 2>/dev/null || echo "GID=$(id -g)" >> .env
curl -o docker-compose.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample-docker-compose-prebuilt.yaml

# Optional: Enable automatic updates before first startup
nano docker-compose.yaml  # Uncomment the watchtower section

# Start containers and view logs
docker compose up -d
docker compose logs -f
```

**That's it!** Your MMRelay is now running with the official prebuilt image.

> **Production deployment**: The `:latest` tag is mutable and may change. For production deployments, pin a specific version tag or digest to ensure reproducible deployments. See the [Kubernetes Guide](KUBERNETES.md#pinning-digests-for-production) for digest pinning examples.

## Deployment Methods

MMRelay supports two deployment approaches:

1. **Prebuilt Images (Recommended)** - Pull official images from GitHub Container Registry
2. **Build from Source** - Build images locally from the Dockerfile

Both use the same base `docker-compose.yaml` file. The difference is whether an optional `docker-compose.override.yaml` file exists:

- **No override file** → Uses prebuilt image
- **Override file present** → Builds from source

If the Quick Start above doesn't work for your setup, choose from these alternatives:

### Prebuilt Images with Make

If you've cloned the repository locally, use the convenient Make commands:

```bash
make setup-prebuilt  # Copy config, .env, and docker-compose.yaml, then opens editor
make run             # Start container (pulls official image)
make logs            # View logs
```

### Portainer/GUI Tools

For users who prefer web-based Docker management:

1. **Create config file on your host:**

   ```bash
   mkdir -p ~/.mmrelay
   curl -o ~/.mmrelay/config.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample_config.yaml
   nano ~/.mmrelay/config.yaml
   ```

2. **In Portainer, create a new Stack with this compose:**
   - Copy content from: [sample-docker-compose-prebuilt.yaml](https://github.com/jeremiah-k/meshtastic-matrix-relay/blob/main/src/mmrelay/tools/sample-docker-compose-prebuilt.yaml)
   - **Important:** Use `MMRELAY_HOST_HOME` environment variable for host paths (the sample compose file already uses this pattern)
   - Set environment variables in Portainer if needed (UID, GID, etc.)

3. **Minimal Portainer compose (if you prefer to start simple):**

   ```yaml
   services:
     mmrelay:
       image: ghcr.io/jeremiah-k/mmrelay:latest
       container_name: meshtastic-matrix-relay
       restart: unless-stopped
       user: "1000:1000" # May need to match your user's UID/GID. See the Troubleshooting section.
       environment:
         - MMRELAY_HOME=/data
         - TZ=UTC
         - PYTHONUNBUFFERED=1
         - MPLCONFIGDIR=/tmp/matplotlib
       volumes:
         - /home/yourusername/.mmrelay:/data:Z # config.yaml, matrix/, logs/, database/, plugins/
   ```

   Replace `/home/yourusername` with your actual home directory.

### Build from Source with Make

For developers who want to build their own image:

```bash
make setup    # Interactive setup - choose "Build from source"
make build    # Build Docker image from source (uses layer caching)
make run      # Start container
make logs     # View logs
```

This creates `docker-compose.yaml` (base with prebuilt image) and `docker-compose.override.yaml` (override to build from source).

### Build from Source without Make

If you prefer not to use Make commands:

```bash
# After cloning the repository:
mkdir -p ~/.mmrelay
cp src/mmrelay/tools/sample_config.yaml ~/.mmrelay/config.yaml
nano ~/.mmrelay/config.yaml  # Edit your settings

# Set up docker compose files
cp src/mmrelay/tools/sample-docker-compose-prebuilt.yaml docker-compose.yaml
cp src/mmrelay/tools/sample-docker-compose-override.yaml docker-compose.override.yaml

# Build and start:
docker compose build
docker compose up -d
docker compose logs -f
```

**How this works:** Docker Compose automatically merges `docker-compose.yaml` (base configuration) with `docker-compose.override.yaml` (build from source override). The override file tells Docker to build locally instead of pulling the prebuilt image.

## Configuration

MMRelay uses a single configuration file: `~/.mmrelay/config.yaml`. All settings are configured in this file, which is mounted into the Docker container.

**Benefits:**

- All settings in one place
- Easy to track changes and version control
- Simple to back up and restore
- No complex environment variable management

**Setup:**

1. Download the sample config: `curl -Lo ~/.mmrelay/config.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample_config.yaml`
2. Edit the file: `nano ~/.mmrelay/config.yaml`
3. Configure your Matrix rooms, Meshtastic connection, and other settings

## Matrix Authentication

MMRelay requires Matrix authentication. Use the auth system for secure authentication with E2EE support.

### Auth System (`mmrelay auth login`)

Run this on your host system (not in Docker):

```bash
mmrelay auth login
```

This creates `~/.mmrelay/matrix/credentials.json` with:

- E2EE support for encrypted rooms
- Persistent device identity (no "new device" notifications)
- Automatic token refresh and key management
- Matrix 2.0 / MAS (Authentication Service) compatibility

The `credentials.json` file is automatically available at `/data/matrix/credentials.json` in the container.

### Authentication Precedence

MMRelay checks for authentication in this order:

1. **`matrix/credentials.json`** (from `mmrelay auth login`) - recommended
2. **`config.yaml` matrix section (password)** - fallback; password in config file automatically creates `matrix/credentials.json`

> **Note**: For security and full functionality, use `mmrelay auth login`. The password-based method in `config.yaml` is available as a fallback option.

## Make Commands Reference

### Setup Commands

- `make setup` - Interactive setup (choose prebuilt vs source build)
- `make setup-prebuilt` - Direct setup with prebuilt image (recommended)
- `make config` - Copy sample files and create directories (config.yaml, .env, docker-compose.yaml)
- `make edit` - Edit config file with your preferred editor

### Switching Between Prebuilt and Source

- `make use-prebuilt` - Switch to prebuilt image (removes override file)
- `make use-source` - Switch to build from source (creates override file)

### Container Management

- `make run` - Start container (prebuilt or source based on override file)
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

Configure your Meshtastic connection in `~/.mmrelay/config.yaml`:

**TCP Connection (recommended):**

```yaml
meshtastic:
  connection_type: tcp
  host: 192.168.1.100 # Your Meshtastic device IP
  port: 4403 # Default Meshtastic TCP port
# Note: MMRelay initiates an outbound TCP connection to the device;
# you do not need a `ports:` mapping in docker-compose.
```

**Serial Connection:**

```yaml
meshtastic:
  connection_type: serial
  serial_port: /dev/ttyUSB0 # Your serial device path
```

For serial connections, add device mapping to docker-compose.yaml:

```yaml
services:
  mmrelay:
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0
```

**BLE Connection:**

```yaml
meshtastic:
  connection_type: ble
  ble_address: "AA:BB:CC:DD:EE:FF" # Your device's MAC address
```

For BLE connections, add to docker-compose.yaml:

**Recommended approach (secure and functional):**

```yaml
services:
  mmrelay:
    network_mode: host # Required for BLE (Linux only)
    security_opt:
      - apparmor=unconfined # Required to allow DBus communication for BLE
    volumes:
      - /var/run/dbus:/var/run/dbus:ro # D-Bus for BlueZ
```

**Alternative approach:**

```yaml
# If apparmor=unconfined is not acceptable, use privileged mode
services:
  mmrelay:
    network_mode: host
    privileged: true
    volumes:
      - /var/run/dbus:/var/run/dbus:ro
```

**Important:** BLE in Docker only works on Linux hosts. Not supported on Docker Desktop for macOS/Windows.

## Data Persistence

Uses the same directories as standalone installation:

- **Home Directory**: `~/.mmrelay/` (mounted to `/data`). This directory on your host contains the configuration (`config.yaml`), and subdirectories for the database (`database/meshtastic.sqlite`), logs (`logs/`), plugins (`plugins/custom/` and `plugins/community/`), and matrix auth/E2EE data (`matrix/credentials.json`, `matrix/store/`).

**Volume Mounting Explanation:**
The Docker compose files mount `~/.mmrelay/` to `/data`. This ensures that all persistent application data, including configuration, stays together in a single directory. On SELinux systems, add `:Z` to volume options to label mounts correctly, e.g., `~/.mmrelay:/data:Z` (the `:Z` label is appended to the container mount portion to correctly label the host-mounted volume).

**Environment Configuration:**
Docker Compose uses environment variables to set container paths. The sample files set:

```yaml
environment:
  - MMRELAY_HOME=/data
```

Inside the container, `MMRELAY_HOME` drives all runtime paths (credentials, database, logs, E2EE store, plugins).

**Custom Host Data Location:**
To use a different location on your host, use the `MMRELAY_HOST_HOME` variable in your `.env` file or compose file:

```bash
MMRELAY_HOST_HOME=/path/to/your/data
```

Note: Use `MMRELAY_HOST_HOME` for host paths to avoid conflict with the container's `MMRELAY_HOME` environment variable.

## Health Checks

The Docker image includes a built-in health check that supports two modes:

**Default mode (no MMRELAY_READY_FILE):**

- Runs `mmrelay doctor` to verify runtime HOME validity and no legacy paths
- Suitable for standalone deployments where you want deeper health checks

**Recommended mode (MMRELAY_READY_FILE set):**

- Checks if a ready file exists at the path you specify
- This is cheap, stable, and matches Kubernetes readiness semantics
- Set `MMRELAY_READY_FILE` to a writable path to enable this mode

**How the ready-file mode works:**

- When MMRelay starts successfully, it creates a ready file at the path you set (example: `/tmp/mmrelay-ready`)
- The health check (`test -f "$MMRELAY_READY_FILE"`) verifies this file exists
- The file is periodically updated (every 60 seconds by default) to show the application is still responsive
- If the application crashes or fails to start, the ready file is not created/removed, and the container is marked as unhealthy

**Benefits:**

- Tools like Watchtower wait for the health check to pass before considering an update successful
- Docker compose shows health status in `docker compose ps`
- Monitoring tools can detect when the app is truly ready vs. just running

**Configuration (recommended for production):**

- Set `MMRELAY_READY_FILE` to enable ready-file mode (example: `/tmp/mmrelay-ready`)
- Ensure the path is writable; for read-only root filesystems, use a mounted path like `/data/mmrelay-ready`
- To customize the heartbeat interval, set `MMRELAY_READY_HEARTBEAT_SECONDS` (default: 60)
- To enable this in Compose, add `MMRELAY_READY_FILE` to the `environment` section (e.g., `- MMRELAY_READY_FILE=/tmp/mmrelay-ready`)

## Troubleshooting

### Common Portainer Issues

**Volume path errors:**

- Ensure paths like `/home/yourusername/.mmrelay/` exist on the host
- Replace `yourusername` with your actual username
- Create directory manually: `mkdir -p ~/.mmrelay`

**Permission errors:**

- Check that the user ID (1000) has access to the mounted directories
- Adjust `UID` and `GID` in environment variables if needed
- Use `chown -R 1000:1000 ~/.mmrelay/` to fix ownership

**Environment variable issues:**

- Portainer doesn't expand `$HOME` - use absolute paths
- Set environment variables in Portainer's stack environment section
- Use `MMRELAY_HOST_HOME` variable for host paths (not `MMRELAY_HOME`, which is the container path)

**Config file not found:**

- Verify the config file exists at the mounted path
- Check the volume mapping in the compose file
- Ensure the file is readable by the container user

### General Docker Issues

**Container won't start:**

- Check logs: `docker compose logs mmrelay`
- Verify config syntax (host):
  `mmrelay config check --config ~/.mmrelay/config.yaml`
- Verify config syntax (container):
  `docker compose exec mmrelay mmrelay config check`
- Ensure all required config fields are set

**Connection issues:**

- For TCP: Verify Meshtastic device IP and port 4403
- For Serial: Check device permissions and path
- For BLE: Ensure host networking is enabled and AppArmor is disabled (`apparmor=unconfined`). Use privileged mode as an alternative if apparmor unconfined is not acceptable.

### BLE-Specific Troubleshooting

**BLE device not found:**

```bash
# Check if Bluetooth is available on host
sudo systemctl status bluetooth
bluetoothctl list

# Verify container can access Bluetooth
docker compose exec mmrelay bluetoothctl list
```

**Permission denied errors:**

- Try the alternative configurations above (capabilities or privileged mode)
- Check D-Bus socket: `docker compose exec mmrelay ls -la /var/run/dbus`
- On SELinux systems, add `:Z` to volume mounts

**D-Bus connection failures:**

```bash
# Test D-Bus connectivity
docker compose exec mmrelay dbus-send --system --dest=org.bluez --print-reply / org.freedesktop.DBus.Introspectable.Introspect
```

**Adapter blocked:**

```bash
# Check and unblock if needed
sudo rfkill unblock bluetooth
```

## Complete Docker Example

Here's a complete example showing the recommended setup:

### Step 1: Set up authentication

```bash
mmrelay auth login
```

### Step 2: Create and configure config.yaml

```bash
mkdir -p ~/.mmrelay
curl -o ~/.mmrelay/config.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample_config.yaml
nano ~/.mmrelay/config.yaml  # Configure your settings
```

### Step 3: Create docker-compose.yaml

```yaml
services:
  mmrelay:
    image: ghcr.io/jeremiah-k/mmrelay:latest
    container_name: meshtastic-matrix-relay
    restart: unless-stopped
    user: "${UID:-1000}:${GID:-1000}"
    environment:
      - MMRELAY_HOME=/data
      - MMRELAY_READY_FILE=/tmp/mmrelay-ready
    volumes:
      # Use MMRELAY_HOST_HOME for host paths (not MMRELAY_HOME to avoid conflict)
      # For SELinux systems (RHEL/CentOS/Fedora), add :Z flag to prevent permission denied errors
      - ${MMRELAY_HOST_HOME:-$HOME}/.mmrelay:/data:Z
      # For non-SELinux systems, you can use:
      # - ${MMRELAY_HOST_HOME:-$HOME}/.mmrelay:/data

    # Lightweight readiness check
    healthcheck:
      test: ["CMD-SHELL", "test -f $${MMRELAY_READY_FILE:-/tmp/mmrelay-ready}"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
```

### Step 4: Start the container

```bash
# The following commands set up your environment to prevent permission issues
grep -q '^MMRELAY_HOST_HOME=' .env 2>/dev/null || echo "MMRELAY_HOST_HOME=${HOME}" >> .env
grep -q '^UID=' .env 2>/dev/null || echo "UID=$(id -u)" >> .env
grep -q '^GID=' .env 2>/dev/null || echo "GID=$(id -g)" >> .env
docker compose up -d
docker compose logs -f
```

**This provides:**

- E2EE support for encrypted Matrix rooms
- Persistent device identity (no "new device" notifications)
- All configuration in one file (`config.yaml`)
- Simple, minimal setup

### Step 5: Verify E2EE Status (Optional)

If you have enabled E2EE, you can verify its status by checking the logs:

```bash
docker compose logs mmrelay | grep -i e2ee
```

Look for messages like:

- "End-to-End Encryption (E2EE) is enabled"
- "Using credentials from /data/matrix/credentials.json"
- "Found X encrypted rooms out of Y total rooms"

## Data Directory Structure

The unified `MMRELAY_HOME` model is now the default. All runtime state lives under `/data` inside the container:

- `matrix/credentials.json` - Matrix authentication credentials (auto-created)
- `database/meshtastic.sqlite` - SQLite database for node information
- `logs/` - Application logs
- `matrix/store/` - E2EE encryption store (if enabled)
- `plugins/custom/` - Custom plugins
- `plugins/community/` - Community plugins

This provides a clean, predictable structure for all persistent data.

## Switching Between Prebuilt and Source Build

MMRelay uses Docker Compose's standard override mechanism to support both prebuilt images and source builds without maintaining separate configuration files.

### How It Works

- **`docker-compose.yaml`** - Base configuration (uses prebuilt image)
- **`docker-compose.override.yaml`** - Optional override (builds from source)

Docker Compose automatically merges these files when you run `docker compose up`. If the override file exists, it builds from source. If not, it uses the prebuilt image.

### Quick Switching

```bash
# Switch to prebuilt image (default)
make use-prebuilt

# Switch to build from source
make use-source
```

**What happens:**

- `use-prebuilt` removes the override file → uses prebuilt image
- `use-source` creates the override file → builds from source

**Benefits:**

- ✅ Your customizations (BLE settings, env vars, etc.) stay in the base file
- ✅ No complex file editing or sed commands
- ✅ Standard Docker Compose pattern
- ✅ Easy to version control (git-ignore the override file)

## Updates

**Prebuilt images:**

- **Automatic updates (Recommended):** Uncomment the Watchtower service in your docker-compose.yaml file to get daily updates at 2 AM
- **Manual updates:** `docker compose pull && docker compose up -d`

**Watchtower Benefits:**

- Automatic security updates
- No manual intervention required
- Cleans up old images to save space
- Only updates MMRelay container (safe for other services)
- Uses maintained fork (nickfedor/watchtower) that fixes Docker API compatibility issues

**Built from source:**

```bash
git pull
make rebuild    # Stop, rebuild with fresh code, and restart
```

## Advanced Configuration

For additional configuration options like message formatting, debug logging, and environment variable overrides, see [Advanced Configuration](ADVANCED_CONFIGURATION.md).
