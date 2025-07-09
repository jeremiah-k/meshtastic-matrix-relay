# Docker Deployment

Simple Docker setup for Meshtastic Matrix Relay.

## Quick Start

**Option 1: One-step setup (recommended for first time):**

```bash
make setup    # Copy config, .env, and docker-compose.yaml, then opens editor
make build    # Build Docker image
make run      # Start container
```

**Option 2: Manual steps:**

```bash
make config   # Copy sample files and create directories
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
- `make build-host` - Build with host networking (for IPv6/DNS issues during build)
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

## Updates

```bash
git pull
make rebuild    # Stop, rebuild with fresh code, and restart
```

## Troubleshooting

### IPv6 and Network Issues

If you experience network connectivity issues during Docker builds (especially with `apt-get` operations), try these solutions:

**Option 1: Use host networking during build (recommended)**
```bash
make build-host    # Uses host network to avoid IPv6 DNS issues
```

**Option 2: Configure docker-compose for network issues**

Edit your `docker-compose.yaml` and uncomment the appropriate network troubleshooting options:

```yaml
# For DNS resolution issues
dns:
  - 8.8.8.8
  - 8.8.4.4

# To disable IPv6 if causing problems
sysctls:
  - net.ipv6.conf.all.disable_ipv6=1
  - net.ipv6.conf.default.disable_ipv6=1

# For complete network isolation bypass (use with caution)
network_mode: host
```

**Option 3: System-level IPv6 configuration**

If IPv6 is causing persistent issues on your system, you can disable it system-wide:

```bash
# Temporarily disable IPv6
sudo sysctl net.ipv6.conf.all.disable_ipv6=1
sudo sysctl net.ipv6.conf.default.disable_ipv6=1

# Make permanent by adding to /etc/sysctl.conf
echo 'net.ipv6.conf.all.disable_ipv6=1' | sudo tee -a /etc/sysctl.conf
echo 'net.ipv6.conf.default.disable_ipv6=1' | sudo tee -a /etc/sysctl.conf
```

### Common Network Issues

- **DNS resolution failures**: Use custom DNS servers or host networking
- **IPv6 connectivity problems**: Disable IPv6 using sysctls
- **Corporate firewalls**: May require host networking mode
- **VPN interference**: Try building with host networking
