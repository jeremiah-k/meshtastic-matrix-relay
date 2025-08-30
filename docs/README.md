# MMRelay Documentation

Welcome to the MMRelay documentation! This directory contains comprehensive guides for setting up and using MMRelay.

## Getting Started

- **[Installation Guide](INSTRUCTIONS.md)** - Complete setup instructions for MMRelay
- **[E2EE Guide](E2EE.md)** - Matrix End-to-End Encryption setup and usage
- **[Docker Guide](DOCKER.md)** - Docker deployment and configuration

## Advanced Configuration

- **[Advanced Configuration](ADVANCED_CONFIGURATION.md)** - Advanced features like message prefixes, debug logging, and plugins

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
├── E2EE.md               # End-to-End Encryption guide
├── DOCKER.md             # Docker deployment guide
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

- **Current Version**: v1.2+
- **Python Requirement**: 3.10+
- **Supported Platforms**: Linux, macOS, Windows (E2EE not available on Windows)
- **Key Features**: Meshtastic ↔ Matrix relay, E2EE support, Docker deployment, Plugin system
