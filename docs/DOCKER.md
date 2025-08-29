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

## Non-Interactive Authentication

For Docker deployments where interactive authentication isn't possible, MMRelay supports automatic credentials creation from your config file:

1. **Edit your config.yaml** and add your Matrix password:

   ```yaml
   matrix:
     homeserver: https://your-matrix-server.org
     bot_user_id: "@your-bot:your-matrix-server.org"
     password: your_matrix_password_here # Add this line
   ```

2. **Remove or comment out** the `access_token` line if present

3. **Start the container** - MMRelay will automatically:
   - Log in to Matrix using your password
   - Create `credentials.json` with secure session tokens
   - Enable E2EE support if configured
   - Continue normal operation

This method is ideal for:

- Docker deployments without interactive terminals
- Automated deployments and CI/CD pipelines
- Users who haven't cloned the repository
- Environments without Python installed locally

**Security Note**: The password is only used once during initial setup to create `credentials.json`. For enhanced security, remove the `password` field from your `config.yaml` after the first successful startup. On SSO/OIDC-only homeservers (password logins disabled), this method will fail—use `mmrelay auth login` instead.
Additionally, restrict file permissions so only your user can read it:

```bash
chmod 600 ~/.mmrelay/config.yaml
```

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
         - /home/yourusername/.mmrelay:/app/data # Includes config.yaml, credentials.json, and all data
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

## Matrix Authentication

MMRelay requires Matrix authentication to connect to your Matrix homeserver. There are two approaches, with the auth system being strongly recommended for security and functionality.

### Auth System (`mmrelay auth login`)

The auth system provides E2EE support and persistent device identity.

```bash
# Run this on your host system (not in Docker)
mmrelay auth login
```

**What this does:**

- Creates `~/.mmrelay/credentials.json` with secure session credentials
- Generates a persistent device ID for your MMRelay instance
- Sets up encryption key storage for E2EE support
- Establishes proper Matrix session lifecycle

**Features:**

- **E2EE Support**: Provides encrypted room participation
- **Persistent Device Identity**: Same device across restarts, no "new device" notifications
- **Automatic Key Management**: Handles encryption keys, sharing, and storage
- **Convenience**: No manual token capture from browser sessions required
- **Secure Storage**: Credentials stored with restricted file permissions (600 on Unix systems)

### Password-based Authentication in config.yaml

Alternative authentication method using a password for automatic credential creation.

```yaml
# In your config.yaml file
matrix:
  homeserver: https://matrix.example.org
  password: your_matrix_password  # Your Matrix account password
  bot_user_id: @yourbot:example.org
```

Note: This method automatically creates `credentials.json` on startup and is compatible with Matrix 2.0/MAS. See the earlier Features list for capabilities; apply the same operational hardening (read-only config bind mount, restrictive file perms).

Compose tip:
```yaml
volumes:
  - ${MMRELAY_HOME}/.mmrelay/config.yaml:/app/config.yaml:ro
  - ${MMRELAY_HOME}/.mmrelay:/app/data
```

### Authentication Precedence

MMRelay checks for authentication in this order:

1. **`credentials.json`** (from auth system) - full features
2. **`config.yaml` matrix section (password-based)** - automatic credential creation; E2EE supported when dependencies are available

## Operational Environment Variables

**These environment variables configure connection and system settings - NOT authentication.** Authentication is handled through the methods described above.

**Security Note:** Environment variables are visible via `docker inspect` and process listings. For stronger secrecy, prefer mounting `credentials.json` from the host with restrictive permissions.

### Meshtastic Connection Settings

Configure how MMRelay connects to your Meshtastic device:

#### Connection Type and Settings

- **`MMRELAY_MESHTASTIC_CONNECTION_TYPE`**: Connection method (`tcp`, `serial`, or `ble`)
- **`MMRELAY_MESHTASTIC_HOST`**: TCP host address (for `tcp` connections)
- **`MMRELAY_MESHTASTIC_PORT`**: TCP port number (for `tcp` connections, default: 4403)
- **`MMRELAY_MESHTASTIC_SERIAL_PORT`**: Serial device path (for `serial` connections, e.g., `/dev/ttyUSB0`)
- **`MMRELAY_MESHTASTIC_BLE_ADDRESS`**: Bluetooth MAC address (for `ble` connections)

#### Operational Settings

- **`MMRELAY_MESHTASTIC_BROADCAST_ENABLED`**: Enable Matrix→Meshtastic messages (`true`/`false`)
- **`MMRELAY_MESHTASTIC_MESHNET_NAME`**: Display name for the mesh network
- **`MMRELAY_MESHTASTIC_MESSAGE_DELAY`**: Delay between messages in seconds (minimum: 2.0)

### System Configuration Settings

Configure logging and database behavior:

#### Logging Settings

- **`MMRELAY_LOGGING_LEVEL`**: Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`)
- **`MMRELAY_LOG_FILE`**: Path to log file (enables file logging when set)

#### Database Settings

- **`MMRELAY_DATABASE_PATH`**: Path to SQLite database file

### Why Environment Variables for These Settings?

Environment variables are ideal for operational settings because they:

- **Change between environments** (development, staging, production)
- **Are not sensitive** (unlike authentication credentials)
- **Make Docker deployment flexible** without editing config files
- **Have clear validation** with helpful error messages

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
- Verify config syntax (host): `mmrelay config check --config ~/.mmrelay/config.yaml`
- Verify config syntax (container): `docker compose exec mmrelay mmrelay config check --config /app/config.yaml`
- Ensure all required config fields are set

**Connection issues:**

- For TCP: Verify Meshtastic device IP and port 4403
- For Serial: Check device permissions and path
- For BLE: Ensure privileged mode and host networking are enabled

## Complete Docker Examples

### Method 1: Auth System + Environment Variables (Recommended for E2EE)

Use `mmrelay auth login` for Matrix authentication and environment variables for operational settings. This method provides full E2EE support.

**Prerequisites for E2EE:**

- **Linux/macOS host**: E2EE is not supported on Windows due to library limitations
- **E2EE-enabled image**: Use `ghcr.io/jeremiah-k/mmrelay:latest` or build with E2EE support

#### Step 1: Set up authentication on your host system

```bash
mmrelay auth login
```

#### Step 2: Create docker-compose.yaml

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
      - MMRELAY_MESHTASTIC_MESHNET_NAME=Home Mesh
      - MMRELAY_LOGGING_LEVEL=INFO
      - MMRELAY_DATABASE_PATH=/app/data/meshtastic.sqlite
    volumes:
      - ${MMRELAY_HOME}/.mmrelay/config.yaml:/app/config.yaml:ro
      - ${MMRELAY_HOME}/.mmrelay:/app/data # credentials.json, E2EE store, logs, DB
```

**This approach provides:**

- E2EE support for encrypted Matrix rooms
- Persistent device identity (no "new device" notifications)
- File-based credential storage
- Flexible operational configuration

### Method 2: Password-based Authentication + Environment Variables

Alternative approach using password-based authentication for automatic credential creation.

#### Step 1: Add password to config.yaml

```yaml
# In your ~/.mmrelay/config.yaml
matrix:
  homeserver: https://matrix.example.org
  password: your_matrix_password
  bot_user_id: @yourbot:example.org

matrix_rooms:
  - id: "#yourroom:example.org"
    meshtastic_channel: 0
```

**Features**

- Automatically creates `credentials.json` on first start
- Compatible with Matrix 2.0/MAS authentication
- E2EE supported when dependencies are available

#### Step 2: Create docker-compose.yaml with E2EE

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
      - MMRELAY_MESHTASTIC_MESHNET_NAME=Home Mesh
      - MMRELAY_LOGGING_LEVEL=INFO
      - MMRELAY_DATABASE_PATH=/app/data/meshtastic.sqlite
    volumes:
      - ${MMRELAY_HOME}/.mmrelay/config.yaml:/app/config.yaml:ro
      - ${MMRELAY_HOME}/.mmrelay:/app/data # credentials.json, E2EE store, logs, DB
```

**Security note:** After the first successful start:
1) Remove the `password` from config.yaml,
2) Ensure strict permissions (e.g., `chmod 600 ~/.mmrelay/config.yaml`),
3) Optionally remount config as read-only in compose (`:ro`).
E2EE is supported with this method when dependencies are available (Linux/macOS).

## Connection Type Variants

The examples above show TCP connections. Here are variants for other connection types:

**Serial Connection:**

```yaml
environment:
  - MMRELAY_MESHTASTIC_CONNECTION_TYPE=serial
  - MMRELAY_MESHTASTIC_SERIAL_PORT=/dev/ttyUSB0
  # ... other settings
devices:
  - /dev/ttyUSB0:/dev/ttyUSB0
```

**BLE Connection:**

```yaml
environment:
  - MMRELAY_MESHTASTIC_CONNECTION_TYPE=ble
  - MMRELAY_MESHTASTIC_BLE_ADDRESS=AA:BB:CC:DD:EE:FF
  # ... other settings
privileged: true # Required for BLE access
```

### Minimal config.yaml

When using environment variables for most settings, your config.yaml only needs:

```yaml
# Optional: Enable E2EE if you are using the 'auth login' method.
# This is not needed for non-encrypted rooms.
# matrix:
#   e2ee:
#     enabled: true

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

**Note**: For E2EE to function properly, you must also:

1. Use the `mmrelay auth login` method for authentication (not manual tokens)
2. Mount the `~/.mmrelay` directory to `/app/data` to persist credentials and the E2EE store, as shown in the examples

The E2EE store directory (`/app/data/store`) is automatically created and persisted via the volume mount.

### Verification

Check logs for E2EE status:

```bash
docker compose logs mmrelay | grep -i e2ee
```

Look for messages like:

- "End-to-End Encryption (E2EE) is enabled"
- "Using credentials from ~/.mmrelay/credentials.json"
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
