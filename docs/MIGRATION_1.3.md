# Migration Guide for v1.3

This guide helps you upgrade from any legacy layout to the v1.3 unified HOME model.

## What Changed in 1.3

MMRelay now uses a single MMRELAY_HOME root for all runtime state:

- Credentials
- Database
- Logs
- E2EE store
- Plugins

For containers, the canonical model remains:

- `MMRELAY_HOME=/data`
- Config mounted at `/app/config.yaml`

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

If you want legacy locations cleaned up after migration, use `mmrelay migrate --move`.
If you need to overwrite existing target files without creating backups, use
`mmrelay migrate --force` (only after confirming your own external backup).

## Deployment-Specific Migration Quick Reference

### Local / systemd / venv installs

1. Upgrade package to 1.3.0.
2. Run:
   - `mmrelay migrate --dry-run`
   - `mmrelay migrate`
   - `mmrelay verify-migration`

### Docker Compose installs

1. Update compose to 1.3 model:
   - `MMRELAY_HOME=/data`
   - one persistent host mount to `/data`
   - config mounted at `/app/config.yaml`
   - reference examples:
     - prebuilt image flow: [`src/mmrelay/tools/sample-docker-compose-prebuilt.yaml`](../src/mmrelay/tools/sample-docker-compose-prebuilt.yaml)
     - local build flow: [`src/mmrelay/tools/sample-docker-compose.yaml`](../src/mmrelay/tools/sample-docker-compose.yaml)
     - environment template: [`src/mmrelay/tools/sample.env`](../src/mmrelay/tools/sample.env)
2. Ensure `.env` host path values are valid absolute paths.
3. Run migration inside container:
   - `docker compose exec mmrelay mmrelay migrate --dry-run`
   - `docker compose exec mmrelay mmrelay migrate`
   - `docker compose exec mmrelay mmrelay verify-migration`

### Kubernetes / Helm installs

1. Keep one PVC mounted at `/data`.
2. Keep config mounted at `/app/config.yaml`.
3. Deploy upgraded manifests/chart and verify in pod:
   - `kubectl exec -n mmrelay <pod> -- mmrelay migrate --dry-run`
   - `kubectl exec -n mmrelay <pod> -- mmrelay migrate`
   - `kubectl exec -n mmrelay <pod> -- mmrelay verify-migration`

### Migration Command Flags

- `--dry-run`: Preview migration actions without changing files.
- `--move`: Move files instead of copy (removes legacy files after successful migration).
- `--force`: Allow overwriting existing files without backup.

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
mmrelay doctor --migration
```

This prints human-readable warnings when legacy data is still present.

## Automatic Migration Rollback

If the `mmrelay migrate` command fails during its execution, it will attempt to automatically roll back any changes made during that run. This process uses internal backups created during the migration.

A successful automatic rollback ensures that:

- Files and directories are restored to their pre-migration state.
- Newly created directories (like `logs/` or `matrix/store/`) that did not exist before are removed to leave no residue.
- The migration state is cleared, allowing you to retry after resolving the issue.

## How to Roll Back Safely (Manual)

If you need to manually undo a successful migration:

1. Stop MMRelay.
2. Restore your manual backup to the pre-upgrade location.
3. Downgrade the package or container image.
4. Start MMRelay.
5. Confirm the service starts and data is intact.

## Kubernetes-Specific Notes

1. Keep a single PVC mounted at `/data`.
2. Keep config mounted at `/app/config.yaml`.
3. Run verification inside the pod:

```bash
kubectl exec -n mmrelay <pod> -- mmrelay verify-migration
```

If verification fails, stop the rollout and restore your previous image and backup.

## Docker-Specific Notes

1. Use a single bind mount or volume at `/data`.
2. Keep config mounted at `/app/config.yaml`.
3. Run verification inside the container:

```bash
docker compose exec mmrelay mmrelay verify-migration
```

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
