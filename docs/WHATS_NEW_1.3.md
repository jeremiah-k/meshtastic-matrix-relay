# What's New in 1.3.0

MMRelay 1.3.0 is a layout and migration release. The main change is a single
runtime root (`MMRELAY_HOME`) for credentials, database, logs, plugins, and E2EE
store.

## Changes

- Unified runtime path model (`MMRELAY_HOME`) across local, Docker, and Kubernetes.
- Added migration workflow:
  - `mmrelay migrate --dry-run`
  - `mmrelay migrate`
  - `mmrelay verify-migration`
  - `mmrelay doctor`
- Transitioned to move-only migration for safety and simplicity.
- Updated deployment docs/examples for 1.3 path behavior.
- Clarified E2EE dependency troubleshooting.

## Migration TL;DR

1. Stop relay.
2. Upgrade to 1.3.0.
3. Preview migration: `mmrelay migrate --dry-run`
4. Apply migration: `mmrelay migrate`
5. Verify: `mmrelay verify-migration` (returns non-zero if action needed)
6. Check diagnostics: `mmrelay doctor` (shows system health info)
7. Start relay.

> **Note**: `verify-migration` is for CI/CD validation (has exit codes), `doctor` is for troubleshooting (informational only).

### Docker users

- Update `docker-compose.yml` to 1.3 model:
  - `MMRELAY_HOME=/data`
  - mount one persistent host path to `/data`
  - mount config at `/data/config.yaml`
  - if using healthcheck, set `MMRELAY_READY_FILE` and check the same path
- Ensure `.env` host-path variables are valid absolute paths on your machine.
- Run migration in container:
  - `docker compose exec mmrelay mmrelay migrate --dry-run`
  - `docker compose exec mmrelay mmrelay migrate`

### Kubernetes/Helm users

- Keep one persistent volume mounted at `/data`.
- Keep config mounted at `/data/config.yaml`.
- Deploy 1.3 chart/manifests, then verify inside pod:
  - `kubectl exec -n mmrelay <pod> -- mmrelay verify-migration`

## Documentation

- Main docs index: [README](README.md)
- Migration guide: [MIGRATION_1.3.md](MIGRATION_1.3.md)
- Docker: [DOCKER.md](DOCKER.md)
- Helm: [HELM.md](HELM.md)
- Kubernetes: [KUBERNETES.md](KUBERNETES.md)
- E2EE: [E2EE.md](E2EE.md)
- Release checklist: [RELEASE_1.3.md](RELEASE_1.3.md)

## Notes

- Meshtastic devices support one active client connection at a time.
