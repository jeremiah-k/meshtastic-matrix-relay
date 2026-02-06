# MMRelay Documentation

Welcome to the MMRelay documentation! This directory contains comprehensive guides for setting up and using MMRelay.

## Getting Started

- **[Installation Guide](INSTRUCTIONS.md)** - Complete setup instructions for MMRelay
- **[Migration Guide for v1.3](MIGRATION_1.3.md)** - Upgrading from older versions to unified HOME model
- **[E2EE Guide](E2EE.md)** - Matrix End-to-End Encryption setup and usage
- **[Docker Guide](DOCKER.md)** - Docker deployment and configuration
- **[Docker Compose Example](docker-compose.yml)** - Quick Docker Compose setup with MMRELAY_HOME
- **[Helm Guide](HELM.md)** - Kubernetes Helm chart deployment guide

## Advanced Configuration

- **[Advanced Configuration](ADVANCED_CONFIGURATION.md)** - Advanced features like message prefixes, debug logging, environment variables, and plugins

## Docker Compose Quick Start

A minimal Docker Compose example is available at `docs/docker-compose.yml`. This provides the fastest way to run MMRelay with persistent data using the unified MMRELAY_HOME model.

**Quick Start:**

```bash
# 1. Copy the example to your project directory
cp docs/docker-compose.yml .

# 2. Replace image tag in docker-compose.yml
#    Change: ghcr.io/jeremiah-k/mmrelay:REPLACE_ME
#    To: ghcr.io/jeremiah-k/mmrelay:<version>
#    (See https://github.com/jeremiah-k/meshtastic-matrix-relay/pkgs/container/mmrelay for available tags)

# 3. Create your config.yaml (or use sample)
# Option A: if you cloned this repository
cp src/mmrelay/tools/sample_config.yaml config.yaml
# Option B: if you only copied docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/v13rc1-2/src/mmrelay/tools/sample_config.yaml -o config.yaml
# Edit config.yaml with your settings

# 4. Start the service
docker compose up -d

# 5. View logs
docker compose logs -f

# 6. Verify configuration
docker compose exec mmrelay python -m mmrelay doctor
```

**Data Location:** All persistent data (credentials, database, logs, plugins, E2EE store) lives in the `./mmrelay-data` directory that Docker Compose creates automatically.

**Migration:** If upgrading from an old layout, run `docker compose exec mmrelay python -m mmrelay migrate --dry-run` first, then `docker compose exec mmrelay python -m mmrelay migrate` to migrate.

For full Docker documentation, see [DOCKER.md](DOCKER.md).

## File Locations

| File          | Purpose               | Location                      |
| ------------- | --------------------- | ----------------------------- |
| Configuration | Main settings         | `~/.mmrelay/config.yaml`      |
| Credentials   | Matrix authentication | `~/.mmrelay/credentials.json` |
| E2EE Store    | Encryption keys       | `~/.mmrelay/store/`           |
| Logs          | Application logs      | `~/.mmrelay/logs/`            |

## Developer Documentation

- **[Constants Reference](dev/CONSTANTS.md)** - Internal configuration constants and values
- **[E2EE Implementation Notes](dev/E2EE_IMPLEMENTATION_NOTES.md)** - Technical details of E2EE implementation

## Documentation Structure

```bash
docs/
├── README.md              # This file - documentation index
├── INSTRUCTIONS.md        # Main installation and setup guide
├── MIGRATION_1.3.md      # Migration guide for upgrading from older versions
├── E2EE.md               # End-to-End Encryption guide
├── DOCKER.md             # Docker deployment guide
├── docker-compose.yml    # Minimal Docker Compose quick-start example
├── HELM.md               # Helm chart deployment guide
├── ADVANCED_CONFIGURATION.md # Advanced configuration options
└── dev/                  # Developer documentation
    ├── CONSTANTS.md      # Configuration constants reference
    └── E2EE_IMPLEMENTATION_NOTES.md  # Technical implementation details
```

## Getting Help

1. **Check the relevant guide** for your specific use case
2. **Review troubleshooting sections** in each guide
3. **Validate your configuration** with `mmrelay config check`
4. **Enable debug logging** for detailed diagnostics
5. **Ask for help** in the MMRelay Matrix room with your configuration and log excerpts

## Version Information

- **Current Version**: v1.3+
- **Python Requirement**: 3.10+
- **Supported Platforms**: Linux, macOS, Windows (E2EE not available on Windows)
- **Key Features**: Meshtastic ↔ Matrix relay, E2EE support, Docker deployment, Plugin system
