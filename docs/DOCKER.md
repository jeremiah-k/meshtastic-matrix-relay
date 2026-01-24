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
- [Troubleshooting](#troubleshooting)
- [Complete Docker Example](#complete-docker-example)
- [Updates](#updates)
- [Advanced Configuration](#advanced-configuration)

## Prerequisites

You need Docker installed on your system. Follow the [official Docker installation guide](https://docs.docker.com/engine/install/).

## Quick Start (Recommended)

**Most users should start here** - prebuilt images without cloning the repository:

```bash
# Create directories and download config
mkdir -p ~/.mmrelay/data ~/.mmrelay/logs
curl -Lo ~/.mmrelay/config.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample_config.yaml

# Adjust permissions and edit the file
chmod 600 ~/.mmrelay/config.yaml
nano ~/.mmrelay/config.yaml

# Set up environment and get docker-compose file
grep -q '^MMRELAY_HOME=' .env 2>/dev/null || echo 'MMRELAY_HOME=$HOME' >> .env
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

## Deployment Methods

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
   mkdir -p ~/.mmrelay/data ~/.mmrelay/logs
   curl -o ~/.mmrelay/config.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample_config.yaml
   nano ~/.mmrelay/config.yaml
   ```

2. **In Portainer, create a new Stack with this compose:**
   - Copy content from: [sample-docker-compose-prebuilt.yaml](https://github.com/jeremiah-k/meshtastic-matrix-relay/blob/main/src/mmrelay/tools/sample-docker-compose-prebuilt.yaml)
   - **Important:** Replace `${MMRELAY_HOME}` with your actual home directory path (e.g., `/home/username`)
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
         - TZ=UTC
         - PYTHONUNBUFFERED=1
         - MPLCONFIGDIR=/tmp/matplotlib
       volumes:
         - /home/yourusername/.mmrelay/config.yaml:/app/config.yaml:ro,Z
         - /home/yourusername/.mmrelay:/app/data:Z # credentials.json, E2EE store, logs, DB
   ```
   Replace `/home/yourusername` with your actual home directory.

### Build from Source with Make

For developers who want to build their own image:

```bash
make setup    # Copy config, .env, and docker-compose.yaml, then opens editor
make build    # Build Docker image from source (uses layer caching)
make run      # Start container
make logs     # View logs
```

### Build from Source without Make

If you prefer not to use Make commands:

```bash
# After cloning the repository:
mkdir -p ~/.mmrelay/data ~/.mmrelay/logs
cp src/mmrelay/tools/sample_config.yaml ~/.mmrelay/config.yaml
nano ~/.mmrelay/config.yaml  # Edit your settings
cp src/mmrelay/tools/sample-docker-compose-prebuilt.yaml docker-compose.yaml

# Build and start:
docker compose build
docker compose up -d
docker compose logs -f
```

**Note:** The `make config` command is still the easiest way to set up the files correctly. Building from source without any Make commands would require manually creating all configuration files and is not recommended.

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

This creates `~/.mmrelay/credentials.json` with:

- E2EE support for encrypted rooms
- Persistent device identity (no "new device" notifications)
- Automatic token refresh and key management
- Matrix 2.0 / MAS (Authentication Service) compatibility

The `credentials.json` file is automatically mounted to `/app/data/credentials.json` in the container.

### Authentication Precedence

MMRelay checks for authentication in this order:

1. **`credentials.json`** (from `mmrelay auth login`) - recommended
2. **`config.yaml` matrix section (password)** - fallback; password in config file automatically creates credentials.json

> **Note**: For security and full functionality, use `mmrelay auth login`. The password-based method in `config.yaml` is available as a fallback option.

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

- **Config**: `~/.mmrelay/config.yaml` (mounted read-only to `/app/config.yaml`)
- **Data Directory**: `~/.mmrelay/` (mounted to `/app/data`). This directory on your host will contain subdirectories for the database (`data/`), logs (`logs/`), and plugins.

**Volume Mounting Explanation:**
The Docker compose files mount `~/.mmrelay/` to `/app/data` for persistent data and separately bind-mount `config.yaml` to `/app/config.yaml` (read-only). This dual-mounting pattern ensures the container can find the config file at its expected canonical path, while keeping all other data in a single directory. On SELinux systems, add `:Z` to volume options to label mounts correctly, e.g., `/app/config.yaml:ro,Z` and `/app/data:Z`.

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
- Verify config syntax (host):
  `mmrelay config check --config ~/.mmrelay/config.yaml`
- Verify config syntax (container):
  `docker compose exec mmrelay mmrelay config check --config /app/config.yaml`
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
mkdir -p ~/.mmrelay/data ~/.mmrelay/logs
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
    volumes:
      # For SELinux systems (RHEL/CentOS/Fedora), add :Z flag to prevent permission denied errors
      - ${MMRELAY_HOME:-$HOME}/.mmrelay/config.yaml:/app/config.yaml:ro,Z
      - ${MMRELAY_HOME:-$HOME}/.mmrelay:/app/data:Z
      # For non-SELinux systems, you can use:
      # - ${MMRELAY_HOME:-$HOME}/.mmrelay/config.yaml:/app/config.yaml:ro
      # - ${MMRELAY_HOME:-$HOME}/.mmrelay:/app/data
```

### Step 4: Start the container

```bash
# The following commands set up your environment to prevent permission issues
grep -q '^MMRELAY_HOME=' .env 2>/dev/null || echo 'MMRELAY_HOME=$HOME' >> .env
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
- "Using credentials from ~/.mmrelay/credentials.json"
- "Found X encrypted rooms out of Y total rooms"

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
