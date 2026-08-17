# MMRelay Documentation

The repository documentation is for **versioned setup, deployment, upgrade, and
operator guidance**. Broader conceptual and community documentation lives in the
[MMRelay wiki](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki).

## Understand MMRelay

- **[How MMRelay Works](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/How-MMRelay-Works)** — meshnet, relay node, channel/room mapping, message flow, and identity boundary
- **[Getting Started with Matrix](https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki/Getting-Started-With-Matrix-&-MM-Relay)** — Matrix basics for new MMRelay operators

## Install and deploy

- **[Installation Guide](INSTRUCTIONS.md)** — pip/pipx setup and primary configuration workflow
- **[Docker Guide](DOCKER.md)** — Docker deployment
- **[Helm Guide](HELM.md)** — Kubernetes deployment with Helm
- **[Kubernetes Guide](KUBERNETES.md)** — static-manifest deployment

## Configure and operate

- **[E2EE Guide](E2EE.md)** — encrypted rooms, device identity, and cross-signing recovery
- **[Advanced Configuration](ADVANCED_CONFIGURATION.md)** — message formatting, packet routing, health checks, debug logging, and environment overrides

## Upgrade and release notes

- **[What's New in 1.4.0](WHATS_NEW_1.4.md)** — 1.4 release summary and upgrade guidance
- **[Migration Guide for v1.3 layout](MIGRATION_1.3.md)** — moving older installations to the unified MMRelay home layout
- **[What's New in 1.3.0](WHATS_NEW_1.3.md)** — historical 1.3 release summary
- **[What's New in 1.2](WHATS_NEW_1.2.md)** — historical 1.2 release notes

## Runtime file locations

| File          | Purpose               | Default location                    |
| ------------- | --------------------- | ----------------------------------- |
| Configuration | Main settings         | `~/.mmrelay/config.yaml`            |
| Credentials   | Matrix authentication | `~/.mmrelay/matrix/credentials.json` |
| E2EE Store    | Encryption keys       | `~/.mmrelay/matrix/store/`          |
| Logs          | Application logs      | `~/.mmrelay/logs/`                  |

Actual paths can vary when `MMRELAY_HOME`, installer-specific locations, or
container mounts are used. The deployment guides document those cases.

## Developer documentation

- **[Constants Reference](dev/CONSTANTS.md)** — internal configuration constants
- **[Testing Guide](dev/TESTING_GUIDE.md)** — project testing patterns and ownership
- **[Windows Installer Build Guide](dev/INNO_SETUP_GUIDE.md)** — installer maintenance
- **[BLE Compatibility Notes](dev/BLE_DUAL_LIBRARY_COMPATIBILITY.md)** — BLE library compatibility design
- **[Matrix Compatibility Plan](dev/MATRIX_DUAL_LIBRARY_COMPATIBILITY_PLAN.md)** — Matrix-provider compatibility design
- **[Archived implementation notes](dev/archive/)** — historical design and migration material

## Getting help

1. Start with the guide for your deployment or problem area.
2. Run `mmrelay config check` for configuration validation.
3. Run `mmrelay doctor` for general diagnostics.
4. Enable targeted debug logging when troubleshooting a connection or provider.
5. Ask in [#mmrelay:matrix.org](https://matrix.to/#/#mmrelay:matrix.org) with relevant configuration excerpts and logs, with secrets removed.
