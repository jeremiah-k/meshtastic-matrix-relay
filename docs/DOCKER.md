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
- [Environment Variables](#environment-variables)
- [Make Commands Reference](#make-commands-reference)
- [Connection Types](#connection-types)
- [Data Persistence](#data-persistence)
- [Troubleshooting](#troubleshooting)
- [Updates](#updates)

## Prerequisites

You need Docker installed on your system. Follow the [official Docker installation guide](https://docs.docker.com/engine/install/).

## Quick Start (Recommended)

**Most users should start here** - prebuilt images without cloning the repository:

```bash
# 1. Create directories and get config
mkdir -p ~/.mmrelay/data ~/.mmrelay/logs
curl -Lo ~/.mmrelay/config.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample_config.yaml

# 2. Edit your config
nano ~/.mmrelay/config.yaml

# 3. Get docker-compose file and start
curl -o docker-compose.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample-docker-compose-prebuilt.yaml
docker compose up -d

# 4. View logs
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
         - /home/yourusername/.mmrelay/config.yaml:/app/config.yaml:ro
         - /home/yourusername/.mmrelay:/app/data
       ports:
         - "4403:4403"
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
make config  # Creates ~/.mmrelay/config.yaml, .env, and docker-compose.yaml
nano ~/.mmrelay/config.yaml  # Edit your settings

# Build and start:
docker compose build
docker compose up -d
docker compose logs -f
```

**Note:** The `make config` command is still the easiest way to set up the files correctly. Building from source without any Make commands would require manually creating all configuration files and is not recommended.

## Environment Variables

The docker-compose files use environment variables for customization:

### Container Configuration
- **`MMRELAY_HOME`**: Base directory for MMRelay data (default: `$HOME`)
- **`UID`**: User ID for container permissions (default: `1000`)
- **`GID`**: Group ID for container permissions (default: `1000`)
- **`EDITOR`**: Preferred text editor for config editing (default: `nano`)

### Matrix Authentication

**⚠️ IMPORTANT: Use the `mmrelay auth login` command for best results**

The **recommended approach** is to use MMRelay's built-in authentication system:

```bash
# Run this on your host system (not in Docker)
mmrelay auth login
```

This creates `~/.mmrelay/credentials.json` with proper E2EE support, persistent device identity, and secure session management.

#### Why the Auth System is Recommended

**✅ Required for E2EE**: Only `mmrelay auth login` provides the device ID and encryption store needed for encrypted rooms

**✅ Persistent Device Identity**: Maintains the same device across restarts, preventing "new device" notifications

**✅ Automatic Key Management**: Handles encryption key upload, sharing, and storage automatically

**✅ More Convenient**: No need to manually capture access tokens from browser sessions

**✅ Secure Session Management**: Proper Matrix session lifecycle with device registration

#### Environment Variable Authentication (Limited)

For cases where the auth system cannot be used, MMRelay supports environment variables:

**Method 1: Individual Variables**
- **`MATRIX_HOMESERVER`**: Matrix homeserver URL (e.g., `https://matrix.example.org`)
- **`MATRIX_ACCESS_TOKEN`**: Matrix access token for authentication (alternative to password)
- **`MATRIX_BOT_USER_ID`**: Bot's Matrix user ID (e.g., `@bot:example.org`)
- **`MATRIX_PASSWORD`**: Matrix password for authentication (alternative to access token)

**Method 2: Base64 Encoded Credentials**
- **`MATRIX_CREDENTIALS_JSON`**: Base64 encoded `credentials.json` content

**⚠️ Limitations of Environment Variable Auth:**
- **No E2EE Support**: Cannot participate in encrypted rooms
- **No Persistent Device ID**: Each restart appears as a new device
- **Manual Token Management**: Requires capturing tokens from browser sessions
- **Security Concerns**: Environment variables visible via `docker inspect`

**Note**: Device ID is never set via environment variables - it comes from the Matrix server during authentication.

### Meshtastic Connection Settings
Configure Meshtastic device connection via environment variables:

**Connection Type and Settings**
- **`MMRELAY_MESHTASTIC_CONNECTION_TYPE`**: Connection method (`tcp`, `serial`, or `ble`)
- **`MMRELAY_MESHTASTIC_HOST`**: TCP host address (for `tcp` connections)
- **`MMRELAY_MESHTASTIC_PORT`**: TCP port number (for `tcp` connections, default: 4403)
- **`MMRELAY_MESHTASTIC_SERIAL_PORT`**: Serial device path (for `serial` connections, e.g., `/dev/ttyUSB0`)
- **`MMRELAY_MESHTASTIC_BLE_ADDRESS`**: Bluetooth MAC address (for `ble` connections)

**Operational Settings**
- **`MMRELAY_MESHTASTIC_BROADCAST_ENABLED`**: Enable Matrix→Meshtastic messages (`true`/`false`)
- **`MMRELAY_MESHTASTIC_MESHNET_NAME`**: Display name for the mesh network
- **`MMRELAY_MESHTASTIC_MESSAGE_DELAY`**: Delay between messages in seconds (minimum: 2.0)

### System Configuration
Configure logging and database settings:

**Logging Settings**
- **`MMRELAY_LOGGING_LEVEL`**: Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)
- **`MMRELAY_LOG_FILE`**: Path to log file (enables file logging when set)

**Database Settings**
- **`MMRELAY_DATABASE_PATH`**: Path to SQLite database file

**Authentication Precedence Order:**
1. Environment variables (highest priority, limited functionality)
2. `credentials.json` file (recommended, full E2EE support)
3. `config.yaml` matrix section (legacy, no E2EE support)

### Setting Environment Variables

**For docker-compose users:** Add to your `.env` file or docker-compose environment section.

**For Portainer users:** You can:
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

- **Config**: `~/.mmrelay/config.yaml` (mounted read-only to `/app/config.yaml`)
- **Data Directory**: `~/.mmrelay/` (mounted to `/app/data`). This directory on your host will contain subdirectories for the database (`data/`), logs (`logs/`), and plugins.

**Volume Mounting Explanation:**
The Docker compose files mount `~/.mmrelay/` to `/app/data` which contains all persistent data (database, logs, plugins). The config file is also mounted separately to `/app/config.yaml` for clarity, even though it's technically accessible via the data mount. This dual mounting ensures the container can find the config file at the expected location.

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

## E2EE (End-to-End Encryption) Setup

MMRelay v1.2+ supports Matrix End-to-End Encryption in Docker environments using environment variables.

### Prerequisites

- **Linux/macOS host**: E2EE is not supported on Windows due to library limitations
- **E2EE-enabled image**: Use `ghcr.io/jeremiah-k/mmrelay:latest` or build with E2EE support

### Setup Methods

**Method 1: Auth System + Docker (Recommended)**

First, create credentials on your host system:

```bash
# Run on host system (not in Docker)
mmrelay auth login
```

Then add to your docker-compose.yaml:

```yaml
services:
  mmrelay:
    image: ghcr.io/jeremiah-k/mmrelay:latest
    environment:
      # Meshtastic Connection (TCP example)
      - MMRELAY_MESHTASTIC_CONNECTION_TYPE=tcp
      - MMRELAY_MESHTASTIC_HOST=192.168.1.100
      - MMRELAY_MESHTASTIC_PORT=4403

      # Operational Settings
      - MMRELAY_MESHTASTIC_BROADCAST_ENABLED=true
      - MMRELAY_MESHTASTIC_MESHNET_NAME=My Mesh Network
      - MMRELAY_LOGGING_LEVEL=INFO
      - MMRELAY_DATABASE_PATH=/app/data/meshtastic.sqlite
    volumes:
      - ~/.mmrelay:/app/data  # Includes credentials.json and E2EE store
      - ~/.mmrelay/config.yaml:/app/config.yaml:ro
```

**✅ This approach provides full E2EE support and persistent device identity.**

**Method 2: Environment Variables Only (Limited)**

For cases where the auth system cannot be used:

```yaml
services:
  mmrelay:
    image: ghcr.io/jeremiah-k/mmrelay:latest
    environment:
      # Matrix Authentication (No E2EE support)
      - MATRIX_HOMESERVER=https://matrix.example.org
      - MATRIX_ACCESS_TOKEN=your_access_token_here
      - MATRIX_BOT_USER_ID=@yourbot:example.org

      # Meshtastic Connection
      - MMRELAY_MESHTASTIC_CONNECTION_TYPE=tcp
      - MMRELAY_MESHTASTIC_HOST=192.168.1.100
      - MMRELAY_MESHTASTIC_PORT=4403

      # Operational Settings
      - MMRELAY_MESHTASTIC_BROADCAST_ENABLED=true
      - MMRELAY_MESHTASTIC_MESHNET_NAME=My Mesh Network
      - MMRELAY_LOGGING_LEVEL=INFO
      - MMRELAY_DATABASE_PATH=/app/data/meshtastic.sqlite
    volumes:
      - ~/.mmrelay:/app/data
      - ~/.mmrelay/config.yaml:/app/config.yaml:ro
```

**⚠️ Note**: Method 2 cannot participate in encrypted Matrix rooms.

**Method 3: Base64 Encoded Credentials**

```yaml
environment:
  - MATRIX_CREDENTIALS_JSON=eyJob21lc2VydmVyIjoi...  # Base64 encoded credentials.json
```

### Complete Configuration Examples

**TCP Connection Example:**
```yaml
services:
  mmrelay:
    image: ghcr.io/jeremiah-k/mmrelay:latest
    environment:
      # Matrix Authentication
      - MATRIX_HOMESERVER=https://matrix.example.org
      - MATRIX_ACCESS_TOKEN=your_access_token_here
      - MATRIX_BOT_USER_ID=@yourbot:example.org


      # Meshtastic TCP Connection
      - MMRELAY_MESHTASTIC_CONNECTION_TYPE=tcp
      - MMRELAY_MESHTASTIC_HOST=192.168.1.100
      - MMRELAY_MESHTASTIC_PORT=4403

      # Operational Settings
      - MMRELAY_MESHTASTIC_BROADCAST_ENABLED=true
      - MMRELAY_MESHTASTIC_MESHNET_NAME=Home Mesh
      - MMRELAY_LOGGING_LEVEL=INFO
      - MMRELAY_DATABASE_PATH=/app/data/meshtastic.sqlite
    volumes:
      - ~/.mmrelay:/app/data
      - ~/.mmrelay/config.yaml:/app/config.yaml:ro  # For matrix_rooms and plugins only
```

**Serial Connection Example:**
```yaml
services:
  mmrelay:
    image: ghcr.io/jeremiah-k/mmrelay:latest
    environment:
      # Matrix Authentication
      - MATRIX_HOMESERVER=https://matrix.example.org
      - MATRIX_ACCESS_TOKEN=your_access_token_here
      - MATRIX_BOT_USER_ID=@yourbot:example.org

      # Meshtastic Serial Connection
      - MMRELAY_MESHTASTIC_CONNECTION_TYPE=serial
      - MMRELAY_MESHTASTIC_SERIAL_PORT=/dev/ttyUSB0

      # Operational Settings
      - MMRELAY_MESHTASTIC_BROADCAST_ENABLED=true
      - MMRELAY_LOGGING_LEVEL=DEBUG
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0
    volumes:
      - ~/.mmrelay:/app/data
      - ~/.mmrelay/config.yaml:/app/config.yaml:ro
```

**BLE Connection Example:**
```yaml
services:
  mmrelay:
    image: ghcr.io/jeremiah-k/mmrelay:latest
    environment:
      # Matrix Authentication
      - MATRIX_HOMESERVER=https://matrix.example.org
      - MATRIX_ACCESS_TOKEN=your_access_token_here
      - MATRIX_BOT_USER_ID=@yourbot:example.org

      # Meshtastic BLE Connection
      - MMRELAY_MESHTASTIC_CONNECTION_TYPE=ble
      - MMRELAY_MESHTASTIC_BLE_ADDRESS=AA:BB:CC:DD:EE:FF

      # Operational Settings
      - MMRELAY_MESHTASTIC_BROADCAST_ENABLED=true
      - MMRELAY_LOGGING_LEVEL=INFO
    privileged: true  # Required for BLE access
    volumes:
      - ~/.mmrelay:/app/data
      - ~/.mmrelay/config.yaml:/app/config.yaml:ro
```

### Minimal config.yaml

When using environment variables for most settings, your config.yaml only needs:

```yaml
matrix_rooms:
  - id: "#yourroom:example.org"
    meshtastic_channel: 0

# Optional: Plugin configuration (if using plugins)
plugins:
  ping:
    active: true
  weather:
    active: true
    units: imperial
```

The E2EE store directory (`/app/data/store`) is automatically created and persisted via the volume mount.

### Verification

Check logs for E2EE status:

```bash
docker compose logs mmrelay | grep -i e2ee
```

Look for messages like:
- "End-to-End Encryption (E2EE) is enabled"
- "Using credentials from environment variables"
- "Found X encrypted rooms out of Y total rooms"

## Updates

**Prebuilt images:**

- Pull latest: `docker compose pull && docker compose up -d`
- Or use Watchtower for automatic updates (see sample-docker-compose-prebuilt.yaml)

**Built from source:**

```bash
git pull
make rebuild    # Stop, rebuild with fresh code, and restart
```
