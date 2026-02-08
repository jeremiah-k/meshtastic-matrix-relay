# Migration Guide for v1.3

This guide helps you upgrade from any legacy layout to the v1.3 unified HOME model.

## What Changed in 1.3

MMRelay now uses a single MMRELAY_HOME root for all runtime state:

- Credentials (moved to `matrix/` subdirectory)
- Database (moved to `database/` subdirectory)
- Logs (moved to `logs/` subdirectory)
- E2EE store (moved to `matrix/store/` subdirectory)
- Plugins (moved to `plugins/` subdirectory)

## New Directory Structure

After migration, your MMRELAY_HOME follows this layout:

```text
~/.mmrelay/  (or /data in containers)
├── config.yaml              # User configuration (optional, can be elsewhere)
├── matrix/                  # Matrix runtime artifacts
│   ├── credentials.json    # Matrix authentication credentials
│   └── store/              # E2EE encryption keys (Unix/macOS only)
├── database/
│   └── meshtastic.sqlite  # SQLite database (with -wal, -shm)
├── logs/
│   └── mmrelay.log          # Application logs
└── plugins/
    ├── core/              # Built-in plugins (read-only, in package)
    ├── custom/            # User plugins
    │   └── <plugin-name>/
    └── community/         # Third-party plugins
        └── <plugin-name>/
```

For containers, the canonical model remains:

- `MMRELAY_HOME=/data`
- Config mounted at `/data/config.yaml`

## Before Upgrading

1. Back up your current MMRELAY_HOME (or the location you use today for MMRelay data).
2. Ensure you can restore the backup.
3. Run a read-only preview: `mmrelay migrate --dry-run`.
4. If you are containerized, make sure you have a single persistent root.

## Upgrade Steps (All Deployments)

1. Stop MMRelay.
2. Upgrade the package or container image to 1.3.
3. Run `mmrelay migrate` to move legacy data into MMRELAY_HOME.
4. Start MMRelay.

Migration in v1.3 uses **move semantics** by default: legacy files are moved to the new structure and removed from their original locations to prevent duplicates.

To ensure safety and atomicity, the migration process follows a **staged move pattern**:

1. Each unit (config, credentials, etc.) is first moved to a temporary staging directory (`MMRELAY_HOME/.migration_staging/`).
2. The staged data is validated for integrity.
3. Upon successful validation, the unit is atomically renamed to its final destination in `MMRELAY_HOME`.

If a destination file already exists, a timestamped backup is **always** created before it is overwritten. These backups are stored in a dedicated directory: `MMRELAY_HOME/.migration_backups/`.

If you need to overwrite existing target files, use `mmrelay migrate --force`. Note that even with `--force`, safety backups of your existing destination data are still created.

## Deployment-Specific Migration Quick Reference

### Local / systemd / venv installs

1. Upgrade package to 1.3.0.
2. Run:
   - `mmrelay migrate --dry-run`
   - `mmrelay migrate`
   - `mmrelay verify-migration`

### Docker Compose installs

See [Docker-Specific Notes](#docker-specific-notes) for detailed instructions.

### Kubernetes / Helm installs

See [Kubernetes-Specific Notes](#kubernetes-specific-notes) for detailed instructions.

### Migration Command Flags

- `--dry-run`: Preview migration actions without changing files. Recommended before applying changes.
- `--force`: Allow overwriting existing files in `MMRELAY_HOME` without creating backups. Use only if you have confirmed your own external backups.

## After Upgrading

1. Run `mmrelay verify-migration` (read-only) to confirm the unified layout.
2. If verification fails, keep MMRelay stopped until you resolve the warnings.

## How to Verify Success

Use the new verification command:

```bash
mmrelay verify-migration
```

This command:

- Prints the resolved MMRELAY_HOME
- Lists credentials, database, E2EE store, and logs paths
- Includes plugins and database directory checks
- Detects legacy data outside MMRELAY_HOME

Exit code semantics:

- `0`: Unified-home is clean (no legacy data, no split roots, credentials present)
- non-zero: Stop and fix before running live (missing credentials or legacy data present)

You can also run:

```bash
mmrelay doctor
```

This prints human-readable warnings when legacy data is still present.

## How to Roll Back Safely (Manual)

If you need to manually undo a successful migration:

1. Stop MMRelay.
2. Restore your manual backup to the pre-upgrade location.
3. Downgrade the package or container image.
4. Start MMRelay.
5. Confirm the service starts and data is intact.

## Kubernetes-Specific Notes

1. Keep one PVC mounted at `/data`.
2. Keep config mounted at `/data/config.yaml`.
3. Deploy upgraded manifests/chart and verify in pod:
   - `kubectl exec -n mmrelay <pod> -- mmrelay migrate --dry-run`
   - `kubectl exec -n mmrelay <pod> -- mmrelay migrate`
   - `kubectl exec -n mmrelay <pod> -- mmrelay verify-migration`

If verification fails, stop the rollout and restore your previous image and backup.

## Docker-Specific Notes

1. Update compose to 1.3 model:
   - `MMRELAY_HOME=/data`
   - Use a single bind mount or volume at `/data`
   - config mounted at `/data/config.yaml`
   - reference examples:
     - prebuilt image flow: [`src/mmrelay/tools/sample-docker-compose-prebuilt.yaml`](../src/mmrelay/tools/sample-docker-compose-prebuilt.yaml)
     - local build flow: [`src/mmrelay/tools/sample-docker-compose.yaml`](../src/mmrelay/tools/sample-docker-compose.yaml)
     - environment template: [`src/mmrelay/tools/sample.env`](../src/mmrelay/tools/sample.env)
2. Ensure `.env` host path values are valid absolute paths.
3. Run migration inside container:
   - `docker compose exec mmrelay mmrelay migrate --dry-run`
   - `docker compose exec mmrelay mmrelay migrate`
   - `docker compose exec mmrelay mmrelay verify-migration`

If verification fails, stop the container, restore your backup, and roll back to the previous image.

## Legacy Examples (Reference Only)

These examples are included only to help you recognize older setups.

### Legacy CLI Flags

<!-- MMRELAY_ALLOW_LEGACY_EXAMPLE -->

```bash
mmrelay --base-dir /opt/mmrelay
mmrelay --data-dir /var/lib/mmrelay
mmrelay --logfile /var/log/mmrelay.log
```

### Legacy Environment Variables

<!-- MMRELAY_ALLOW_LEGACY_EXAMPLE -->

```bash
export MMRELAY_BASE_DIR=/opt/mmrelay
export MMRELAY_DATA_DIR=/var/lib/mmrelay
export MMRELAY_CREDENTIALS_PATH=/opt/mmrelay/credentials.json
```

### Legacy Docker Compose

<!-- MMRELAY_ALLOW_LEGACY_EXAMPLE -->

```yaml
services:
  mmrelay:
    image: ghcr.io/jeremiah-k/mmrelay:1.2.9
    environment:
      - MMRELAY_BASE_DIR=/app/data
      - MMRELAY_CREDENTIALS_PATH=/app/data/credentials.json
    volumes:
      - /host/data:/app/data
      - /host/logs:/app/logs
```
