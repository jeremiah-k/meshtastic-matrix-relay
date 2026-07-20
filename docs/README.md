# MMRelay Documentation

Welcome to the MMRelay documentation! This directory contains comprehensive guides for setting up and using MMRelay.

## Getting Started

- **[Installation Guide](INSTRUCTIONS.md)** - Complete setup instructions for MMRelay
- **[E2EE Guide](E2EE.md)** - Encrypted Matrix rooms (Matrix E2EE) setup and usage
- **[Docker Guide](DOCKER.md)** - Docker deployment and configuration
- **[Helm Guide](HELM.md)** - Kubernetes Helm chart deployment guide

## Advanced Configuration

- **[Advanced Configuration](ADVANCED_CONFIGURATION.md)** - Advanced features like message prefixes, packet routing overrides, health-check tuning, debug logging, and environment variables

## Release-Specific Documents

- **[What's New in 1.4.0](WHATS_NEW_1.4.md)** - Mesh observability, BLE recovery, and Python 3.11 upgrade guidance
- **[What's New in 1.2](WHATS_NEW_1.2.md)** - Previous release notes (historical)
- **[Archived 1.3 Migration Guide](dev/archive/MIGRATION_1.3.md)** - For systems still on 1.2.x or older before upgrading through a 1.3.x release
- **[Archived What's New in 1.3.0](dev/archive/WHATS_NEW_1.3.md)** - Historical 1.3 release summary

## File Locations

| File          | Purpose               | Location                             |
| ------------- | --------------------- | ------------------------------------ |
| Configuration | Main settings         | `~/.mmrelay/config.yaml`             |
| Credentials   | Matrix authentication | `~/.mmrelay/matrix/credentials.json` |
| E2EE Store    | Encryption keys       | `~/.mmrelay/matrix/store/`           |
| Logs          | Application logs      | `~/.mmrelay/logs/`                   |

## Developer Documentation

- **[Constants Reference](dev/CONSTANTS.md)** - Internal configuration constants and values
- **[E2EE Implementation Notes](dev/archive/E2EE_IMPLEMENTATION_NOTES.md)** - Technical details of E2EE implementation

## Documentation Structure

```bash
docs/
├── README.md                 # This file - documentation index
├── INSTRUCTIONS.md           # Main installation and setup guide
├── MIGRATION_1.3.md          # Compatibility link to the archived guide
├── WHATS_NEW_1.4.md          # 1.4 features and upgrade guidance
├── WHATS_NEW_1.3.md          # Compatibility link to archived release notes
├── WHATS_NEW_1.2.md          # 1.2 release notes (historical)
├── E2EE.md                  # End-to-End Encryption guide
├── DOCKER.md                # Docker deployment guide
├── KUBERNETES.md            # Static manifest deployment guide
├── HELM.md                  # Helm chart deployment guide
├── ADVANCED_CONFIGURATION.md # Advanced configuration options
└── dev/                     # Developer documentation
    ├── CONSTANTS.md         # Internal configuration constants
    ├── INNO_SETUP_GUIDE.md  # Windows installer build guide
    ├── TESTING_GUIDE.md     # Testing patterns and practices
    └── archive/             # Historical planning documents
        ├── DATA_LAYOUT_MIGRATION.md
        ├── MIGRATION_1.3.md  # Archived 1.3 migration guide
        ├── WHATS_NEW_1.3.md  # Archived 1.3 release summary
        ├── E2EE_IMPLEMENTATION_NOTES.md  # E2EE technical details
        ├── V1_3_DIRECTORY_REDESIGN.md
        ├── V1_3_MIGRATION_IMPROVEMENTS_PLAN.md
        ├── UPGRADE_TEST_PLAN.md
        └── UPGRADE_TEST_EXECUTION_CHECKLIST.md
```

## Getting Help

1. **Check the relevant guide** for your specific use case
2. **Review troubleshooting sections** in each guide
3. **Validate your configuration** with `mmrelay config check`
4. **Enable debug logging** for detailed diagnostics
5. **Ask for help** in the MMRelay Matrix room with your configuration and log excerpts

## Version Information

- **Next Release**: v1.4.0
- **Python Requirement (v1.4+)**: 3.11+
- **Supported Platforms**: Linux, macOS, Windows (E2EE not available on Windows)
- **Key Features**: Meshtastic ↔ Matrix relay, encrypted Matrix rooms (Matrix E2EE), Docker deployment, Plugin system
