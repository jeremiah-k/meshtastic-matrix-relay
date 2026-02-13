# What's New in 1.3.0

MMRelay 1.3.0 introduces a unified runtime directory structure and streamlined migration tools.

## Changes

- **Unified runtime path model** (`MMRELAY_HOME`) across local, Docker, and Kubernetes deployments
- **New migration commands** for safe data migration:
  - `mmrelay migrate --dry-run` - Preview migration without changes
  - `mmrelay migrate` - Apply migration
  - `mmrelay verify-migration` - Validate migration (CI/CD friendly)
  - `mmrelay doctor` - System diagnostics and troubleshooting
- **Simplified directory structure**: All runtime data (credentials, database, logs, plugins, E2EE store) organized under a single home directory
- **Move-only migration** for safety and simplicity
- **Updated deployment documentation** for the 1.3 path behavior
- **Improved E2EE dependency troubleshooting**

## Migration

If upgrading from v1.2 or earlier, see the **[Migration Guide](MIGRATION_1.3.md)** for detailed instructions.

Quick overview:

1. Stop MMRelay
2. Upgrade to 1.3.0
3. Run `mmrelay migrate --dry-run` to preview
4. Run `mmrelay migrate` to apply
5. Run `mmrelay verify-migration` to confirm
6. Start MMRelay

## Documentation

- **[Installation Guide](INSTRUCTIONS.md)** - Setup and configuration
- **[Migration Guide](MIGRATION_1.3.md)** - Upgrading from v1.2
- **[Docker Guide](DOCKER.md)** - Docker deployment
- **[Helm Guide](HELM.md)** - Kubernetes with Helm
- **[Kubernetes Guide](KUBERNETES.md)** - Static manifests
- **[E2EE Guide](E2EE.md)** - End-to-End Encryption

## Notes

- Meshtastic devices support one active client connection at a time
- Legacy credential/location fallback is supported until v1.4; migrate before upgrading to v1.4
