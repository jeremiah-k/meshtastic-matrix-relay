# Kubernetes Deployment Guide

> **Note**: Kubernetes deployment is currently in testing and development. We welcome feedback to help improve the manifests and deployment experience.

This guide uses the static manifests in `deploy/k8s/`. Download them, create a Secret with your `config.yaml`, then apply.

## Prerequisites

- Kubernetes cluster (v1.20+)
- `kubectl` (includes kustomize support for `kubectl apply -k`)

## Upgrading to 1.3

If you are upgrading from 1.2.x or earlier, read and follow
`docs/MIGRATION_1.3.md` before applying the manifests. New installations can
proceed with the Quick Start below.

## Image Selection

The base Kubernetes manifest uses `kustomize` images transform to set the container image tag. By default, the base configuration uses the `latest` tag. For 1.3.x, set a specific tag such as `1.3.0`.

### Setting a specific image tag

Edit `deploy/k8s/kustomization.yaml` to set a specific tag:

```yaml
images:
  - name: ghcr.io/jeremiah-k/mmrelay
    newTag: 1.3.0 # Change to your desired version
```

Alternatively, use `kustomize edit` from the command line:

```bash
kustomize edit set image ghcr.io/jeremiah-k/mmrelay:<tag>
```

### Pinning digests for production

For production deployments, use the digest overlay to pin a specific image digest. This provides immutable image references.

1. Find the digest for your desired tag:

   ```bash
   skopeo inspect docker://ghcr.io/jeremiah-k/mmrelay:<tag>
   ```

2. Update `deploy/k8s/overlays/digest/kustomization.yaml` with the digest:

   ```yaml
   images:
     - name: ghcr.io/jeremiah-k/mmrelay
       digest: sha256:abc123... # Replace with actual digest
   ```

3. Apply the overlay:
   ```bash
   kubectl apply -k ./deploy/k8s/overlays/digest
   ```

Tags and digests are listed on the GitHub Packages page:
[https://github.com/jeremiah-k/meshtastic-matrix-relay/pkgs/container/mmrelay](https://github.com/jeremiah-k/meshtastic-matrix-relay/pkgs/container/mmrelay)

## Quick Start (new install, static manifests)

```bash
# Create a project directory and change into it
mkdir -p mmrelay
cd mmrelay

# Download manifests from the main branch
BASE_URL="https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/deploy/k8s"
mkdir -p ./deploy/k8s/overlays/digest
curl -fLo ./deploy/k8s/pvc.yaml "${BASE_URL}/pvc.yaml"
curl -fLo ./deploy/k8s/networkpolicy.yaml "${BASE_URL}/networkpolicy.yaml"
curl -fLo ./deploy/k8s/deployment.yaml "${BASE_URL}/deployment.yaml"
curl -fLo ./deploy/k8s/kustomization.yaml "${BASE_URL}/kustomization.yaml"
curl -fLo ./deploy/k8s/overlays/digest/kustomization.yaml "${BASE_URL}/overlays/digest/kustomization.yaml"

# Optional: edit kustomization.yaml to set a specific image tag
${EDITOR:-vi} ./deploy/k8s/kustomization.yaml
# The default uses latest; set newTag to 1.3.0 (or another release tag) for stability
# If you change the namespace, update the --namespace/-n flags below

# Ensure the namespace exists
kubectl create namespace mmrelay --dry-run=client -o yaml | kubectl apply -f -

# Create config.yaml from the project sample
curl -Lo ./config.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample_config.yaml
${EDITOR:-vi} ./config.yaml

# The default manifest sets MMRELAY_HOME=/data, so credentials,
# database, logs, and E2EE store will all persist on the PVC.
# All runtime state lives under /data inside the container.
# No legacy environment variables or CLI flags are required for container deployments.

# Create a Matrix auth secret (environment-based auth)
# The password is entered interactively and will not be shown or stored in shell history
kubectl create secret generic mmrelay-matrix-auth \
  --from-literal=MMRELAY_MATRIX_HOMESERVER=$(read -p "Matrix homeserver URL (e.g., https://matrix.example.org): "; echo "$REPLY") \
  --from-literal=MMRELAY_MATRIX_BOT_USER_ID=$(read -p "Matrix bot user ID (e.g., @bot:example.org): "; echo "$REPLY") \
  --from-literal=MMRELAY_MATRIX_PASSWORD=$(read -s -p "Matrix password: "; echo >&2; echo "$REPLY") \
  --namespace mmrelay

# Store config.yaml in a Kubernetes Secret
kubectl create secret generic mmrelay-config \
  --from-file=config.yaml=./config.yaml \
  --namespace mmrelay

# Apply manifests
kubectl apply -k ./deploy/k8s

# Check status
kubectl get pods -n mmrelay -l app=mmrelay
kubectl logs -n mmrelay -f deployment/mmrelay
```

## Secrets and configuration

The deployment mounts a Secret named `mmrelay-config` with one key:

- `config.yaml`

Authentication secrets are provided separately using environment variables
via the optional `mmrelay-matrix-auth` Secret (see example above). On first
startup, MMRelay will log in with the provided credentials and create
`/data/credentials.json` on the persistent volume.

This keeps sensitive data out of the manifests so you can publish the manifests without exposing secrets. If you use an external secrets manager (External Secrets, Sealed Secrets, Vault, etc.), create the same Secret name/keys.

### Config injection options

MMRelay supports two patterns for injecting `config.yaml`:

#### Pattern A (default): Secret

The default manifest uses a Secret to mount `config.yaml`:

```bash
kubectl create secret generic mmrelay-config \
  --from-file=config.yaml=./config.yaml \
  --namespace mmrelay
```

This is the recommended approach because:

- Secrets can integrate with external secret managers (External Secrets, Sealed Secrets, Vault)
- Secrets are not logged or tracked in clear text by default
- Supports rotation via external secret management systems

#### Pattern B (optional): ConfigMap

If you prefer ConfigMaps (e.g., for non-sensitive config), uncomment the ConfigMap pattern in `deployment.yaml`:

1. Create the ConfigMap:

   ```bash
   kubectl create configmap mmrelay-config \
     --from-file=config.yaml=./config.yaml \
     --namespace mmrelay
   ```

2. Uncomment the ConfigMap volume and volumeMount in `deployment.yaml`:
   - In the `volumes` section, uncomment the ConfigMap volume
   - In `spec.template.spec.containers[0].volumeMounts`, uncomment the ConfigMap mount
   - Comment out the Secret volume and mount

**Important**: Only enable one pattern at a time (Secret OR ConfigMap), not both.

### Credentials injection

MMRelay includes a recommended pattern for injecting `credentials.json` from a Secret. This approach is safer than editing files inside running containers and enables credential rotation.

#### Create the credentials Secret

Generate credentials.json locally (or copy from an existing installation):

```bash
# Option 1: Create credentials.json locally (run locally with same config.yaml)
mmrelay auth login

# Option 2: Copy existing credentials from another deployment
# Ensure the Matrix homeserver and bot user match your config.yaml
cp ~/.mmrelay/credentials.json ./credentials.json
```

Create the Secret:

```bash
kubectl create secret generic mmrelay-credentials \
  --from-file=credentials.json=./credentials.json \
  --namespace mmrelay
```

#### Enable credentials Secret in deployment

1. Uncomment the credentials Secret volume in `deployment.yaml`:
   - In the `volumes` section, uncomment the credentials Secret volume
   - In `spec.template.spec.containers[0].volumeMounts`, uncomment the credentials mount

2. Delete the optional `mmrelay-matrix-auth` Secret (if used):

   ```bash
   kubectl delete secret mmrelay-matrix-auth -n mmrelay
   ```

3. Restart the pod:
   ```bash
   kubectl delete pod -n mmrelay -l app=mmrelay
   ```

The pod will start using the mounted `credentials.json` instead of bootstrapping from environment variables.

#### Rotate credentials

To rotate credentials:

1. Generate new credentials.json locally:

   ```bash
   mmrelay auth login  # This overwrites existing credentials.json
   ```

2. Update the Secret:

   ```bash
   kubectl create secret generic mmrelay-credentials \
     --from-file=credentials.json=./credentials.json \
     --dry-run=client -o yaml \
     --namespace mmrelay | kubectl apply -f -
   ```

3. Restart the pod:
   ```bash
   kubectl delete pod -n mmrelay -l app=mmrelay
   ```

The new credentials will be loaded on the next startup.

#### Alternative: Environment-based auth (bootstrap mode)

The default deployment includes an optional `mmrelay-matrix-auth` Secret for bootstrap mode. On first startup, MMRelay:

1. Reads Matrix credentials from environment variables
2. Logs into Matrix
3. Creates `/data/credentials.json` on the PVC
4. On subsequent restarts, uses the existing credentials.json

This is useful for:

- Initial deployment when you don't have credentials.json yet
- Environments where Secret rotation is handled externally

Note: Once credentials.json exists, the environment variables are no longer needed.

## Storage

The deployment uses `/data` as the base directory for all persistent data:

- **Credentials**: `/data/credentials.json` (auto-created on first login)
- **Logs**: `/data/logs/`
- **Database**: `/data/database/meshtastic.sqlite`
- **E2EE store**: `/data/store/` (if encryption is enabled)
- **Plugins (custom)**: `/data/plugins/custom/`
- **Plugins (community)**: `/data/plugins/community/`

This is configured in `deployment.yaml` via `MMRELAY_HOME=/data` and the PVC mount. All data persists across pod restarts.

`./deploy/k8s/pvc.yaml` uses the cluster default StorageClass. If your cluster requires a specific StorageClass, add `storageClassName` there.

## Backup, restore, and disaster recovery

### Runtime state location

All MMRelay runtime state lives under `/data` inside the container:

```text
/data/
├── credentials.json      # Matrix authentication credentials
├── meshtastic.sqlite           # SQLite database (nodes, messages, state)
├── logs/               # Application logs
├── store/              # E2EE encryption keys (if enabled)
└── plugins/            # Custom and community plugins
    ├── custom/
    └── community/
    ├── custom/
    └── community/
```

The PVC is the authoritative source for all persistent data. Backing up the PVC preserves your complete MMRelay state.

### Backup

#### Method 1: PVC snapshot (recommended)

Most Kubernetes storage providers support volume snapshots:

```bash
# Create a snapshot of the mmrelay-data PVC
kubectl create volumesnapshot mmrelay-data-backup-$(date +%Y%m%d) \
  --source=persistentvolumeclaim/mmrelay-data \
  --namespace mmrelay
```

Check your cloud provider's documentation for:

- Snapshot creation limits (frequency, retention)
- Snapshot-to-PVC restoration procedure
- Cost implications of snapshots

#### Method 2: rsync backup

Create a backup to local storage:

```bash
# Get the pod name
POD_NAME=$(kubectl get pods -n mmrelay -l app=mmrelay -o jsonpath='{.items[0].metadata.name}')

# Copy /data to local directory
kubectl exec -n mmrelay $POD_NAME -- tar czf - /data > mmrelay-backup-$(date +%Y%m%d).tar.gz
```

For larger deployments, use rsync:

```bash
# Create a temporary pod with the PVC
kubectl run backup-pod \
  --image=busybox:1.36 \
  --overrides='{
    "spec": {
      "containers": [{
        "name": "backup",
        "image": "busybox:1.36",
        "command": ["sleep", "3600"],
        "volumeMounts": [{
          "name": "data",
          "mountPath": "/data"
        }]
      }],
      "volumes": [{
        "name": "data",
        "persistentVolumeClaim": {
          "claimName": "mmrelay-data"
        }
      }]
    }
  }' \
  --namespace mmrelay

# Copy data from the temporary pod
kubectl cp -n mmrelay backup-pod:/data ./mmrelay-backup

# Clean up the temporary pod
kubectl delete pod backup-pod -n mmrelay
```

### Restore

#### From PVC snapshot

Restore a snapshot (procedure varies by storage provider):

```bash
# Example: Create a new PVC from a snapshot
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: mmrelay-data-restored
  namespace: mmrelay
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: <your-storage-class>
  resources:
    requests:
      storage: 1Gi
  dataSource:
    name: mmrelay-data-backup-20250101
    kind: VolumeSnapshot
    apiGroup: snapshot.storage.k8s.io
EOF

# Update deployment to use the restored PVC
kubectl set volume deployment/mmrelay -n mmrelay \
  --name=data \
  --overwrite \
  --pvc-name=mmrelay-data-restored
```

#### From backup archive

```bash
# Stop the pod
kubectl scale deployment mmrelay -n mmrelay --replicas=0

# Create a temporary pod with the PVC
kubectl run restore-pod \
  --image=busybox:1.36 \
  --overrides='{
    "spec": {
      "containers": [{
        "name": "restore",
        "image": "busybox:1.36",
        "command": ["sleep", "3600"],
        "volumeMounts": [{
          "name": "data",
          "mountPath": "/data"
        }]
      }],
      "volumes": [{
        "name": "data",
        "persistentVolumeClaim": {
          "claimName": "mmrelay-data"
        }
      }]
    }
  }' \
  --namespace mmrelay

# Copy the backup to the PVC
kubectl cp ./mmrelay-backup -n mmrelay restore-pod:/data/

# Clean up the temporary pod
kubectl delete pod restore-pod -n mmrelay

# Start the pod
kubectl scale deployment mmrelay -n mmrelay --replicas=1
```

### Migration

The `mmrelay migrate` command handles data migrations between versions:

```bash
# Dry-run: Preview changes without making them
kubectl exec -n mmrelay <pod-name> -- mmrelay migrate --dry-run --home /data

# Perform migration
kubectl exec -n mmrelay <pod-name> -- mmrelay migrate --home /data

# Force migration (if files already exist in target)
kubectl exec -n mmrelay <pod-name> -- mmrelay migrate --force --home /data
```

The migration command is designed to be idempotent and safe. It:

- Detects legacy directory structures
- Moves files to the unified `/data` layout
- Creates necessary directories
- Preserves your existing data

For detailed migration instructions, see the [Migration Guide for v1.3](MIGRATION_1.3.md).

### Disaster recovery checklist

1. **Prevention**:
   - Enable PVC snapshots (if supported by your storage provider)
   - Set up regular backup schedules (cron, Velero, etc.)
   - Test backup restoration procedures regularly

2. **Detection**:
   - Monitor pod health (liveness/readiness probes)
   - Check PVC status (`kubectl get pvc -n mmrelay`)
   - Verify disk space usage (`kubectl exec -n mmrelay <pod> -- df -h /data`)

3. **Recovery**:
   - Restore from the most recent backup
   - Verify the pod starts successfully
   - Run `mmrelay doctor` to validate the installation
   - Check logs for any errors after restoration

## Operator safety notes

### Safe to delete

The following resources can be safely deleted and will be recreated automatically:

- **Pods**: Deleting a pod triggers Kubernetes to create a replacement pod.
- **Deployments**: Deleting the deployment requires you to re-apply the manifest.
- **ConfigMaps and Secrets**: These can be updated (not deleted) without affecting running pods until rollout.

```bash
# Safe: Delete a pod (will be recreated)
kubectl delete pod -n mmrelay <pod-name>

# Safe: Force a pod restart via deployment
kubectl rollout restart deployment mmrelay -n mmrelay
```

### Must never delete

The following resources contain persistent data and must **never** be deleted:

- **PersistentVolumeClaim (PVC)**: `mmrelay-data`
  - Contains all runtime state (credentials, database, logs, E2EE keys, plugins)
  - Deleting the PVC results in **permanent data loss**

```bash
# NEVER run this command
kubectl delete pvc mmrelay-data -n mmrelay  # DANGEROUS - permanent data loss
```

If you need to reset the PVC:

1. Scale down the deployment (`kubectl scale deployment mmrelay -n mmrelay --replicas=0`)
2. Delete the PVC (`kubectl delete pvc mmrelay-data -n mmrelay`) - **only if you have a backup**
3. Re-apply the PVC manifest
4. Scale up the deployment

### Auto-recreated

The following paths recreate themselves automatically on startup:

- `/run/mmrelay/`: Runtime directory (contains the ready file)
- Caches: Temporary data cached in memory or temporary files
- Logs: New log files are created on startup (old logs are retained in `/data/logs/`)

These are **not** persistent and should not be backed up.

### Authoritative data

The PVC is the **single source of truth** for persistent data:

- `/data` (PVC): **Authoritative** - persistent, backed up
- `/app/config.yaml`: **Not persistent** - injected from Secret/ConfigMap, not backed up
- `/run/mmrelay`: **Not persistent** - recreated on each pod start
- `/tmp`: **Not persistent** - temporary storage

When debugging or troubleshooting, always verify the contents of `/data` on the PVC.

## Operational model

### Health probes

MMRelay uses Kubernetes startup, readiness, and liveness probes to ensure the pod is operating correctly:

**Readiness probe** (period: 10s, timeout: 2s, failureThreshold: 3):

- Checks if the ready file exists at `/run/mmrelay/ready`
- Cheap and stable check that determines service routing
- The pod is marked "Ready" when the ready file exists
- Traffic is only sent to ready pods

**Startup probe** (period: 5s, timeout: 2s, failureThreshold: 60):

- Also checks for the ready file at `/run/mmrelay/ready`
- Allows up to 5 minutes for initialization (60 failures × 5s = 300s)
- Prevents the liveness probe from killing the pod during slow startup
- Once the startup probe succeeds, the liveness probe takes over

**Liveness probe** (period: 60s, timeout: 20s, failureThreshold: 3):

- Runs `mmrelay doctor` for deeper health checks
- Checks if MMRelay is still healthy after startup completes
- If the probe fails repeatedly, Kubernetes will restart the pod
- The longer period and timeout reduce false positives for transient issues

`mmrelay doctor` verifies:

- Runtime HOME (`/data`) is valid
- Path resolution is working correctly
- Legacy data sources are detected (if any)
- Migration status is reported

**Why this split?**

- Readiness determines service routing; it should be cheap and stable (ready-file check)
- StartupProbe prevents crashloops on slow initialization by disabling liveness checks during startup
- Liveness can be deeper and slower because it runs infrequently and only after startup succeeds

### Troubleshooting probe failures

If a pod is not ready or keeps restarting:

1. Check the pod logs:

   ```bash
   kubectl logs -n mmrelay <pod-name>
   ```

2. Verify the ready file exists:

   ```bash
   kubectl exec -n mmrelay <pod-name> -- ls -l /run/mmrelay
   ```

3. Run doctor inside the pod:

   ```bash
   kubectl exec -n mmrelay <pod-name> -- mmrelay doctor
   ```

4. Verify the config Secret is mounted:

   ```bash
   kubectl exec -n mmrelay <pod-name> -- cat /app/config.yaml
   ```

5. Check the persistent volume claim status:
   ```bash
   kubectl get pvc -n mmrelay mmrelay-data
   ```

### Graceful shutdown

MMRelay implements safe shutdown via a Kubernetes `preStop` lifecycle hook:

```bash
# Send SIGTERM to allow graceful shutdown
sleep 5 || true
```

This gives MMRelay time to:

- Flush the database to disk
- Store any pending state
- Disconnect cleanly from the mesh network

The deployment sets `terminationGracePeriodSeconds: 30`, which allows the preStop hook and process cleanup to complete before Kubernetes sends `SIGKILL`.

### Data directory ownership

Even with `fsGroup: 1000` set, some CSI drivers mount volumes owned by root. To ensure MMRelay can write to `/data`, an initContainer runs before the main container starts:

```yaml
initContainers:
  - name: fix-data-perms
    image: busybox:1.36
    command: ["chown", "-R", "1000:1000", "/data"]
```

This guarantees:

- MMRelay (running as UID/GID 1000) can write credentials, database, plugins, and logs
- Works across different storage backends (NFS, Ceph, local, etc.)

The initContainer runs as root (`runAsUser: 0`) to modify ownership, then the main container runs as the non-root user (1000:1000).

## Verification

After deployment, verify your configuration:

```bash
# Get the pod name
POD_NAME=$(kubectl get pods -n mmrelay -l app=mmrelay -o jsonpath='{.items[0].metadata.name}')

# Run diagnostics
kubectl exec -n mmrelay $POD_NAME -- mmrelay doctor --config /app/config.yaml

# Verify paths
kubectl exec -n mmrelay $POD_NAME -- mmrelay paths --config /app/config.yaml
```

**Expected output (summary)**:

<!-- MMRELAY_ALLOW_LEGACY_EXAMPLE -->

```text
- HOME is `/data`
- No legacy environment variables (MMRELAY_CREDENTIALS_PATH, MMRELAY_BASE_DIR, MMRELAY_DATA_DIR) are set
- All runtime paths resolve under `/data`
```

## Connection types

### TCP (recommended)

No manifest changes required. Configure `meshtastic.connection_type: tcp` in `config.yaml`.

### Serial

Serial requires host device access and node pinning. Start with the most restrictive settings and only escalate if needed.

1.  Add the device mount to the container:

    In `./deploy/k8s/deployment.yaml`, add this entry under
    `spec.template.spec.containers[0].volumeMounts`:

    ```yaml
    - name: serial-device
      mountPath: /dev/ttyUSB0
    ```

2.  Add the hostPath volume:

    In the same file, add this under `spec.template.spec.volumes`:

    ```yaml
    - name: serial-device
      hostPath:
        path: /dev/ttyUSB0
        type: CharDevice
    ```

3.  Pin the pod to the node with the device:

    Add this under `spec.template.spec`:

    ```yaml
    nodeSelector:
      kubernetes.io/hostname: node-with-device
    ```

4.  Add pod-level security context for supplemental groups:

    Add this under `spec.template.spec`:

    ```yaml
    securityContext:
      supplementalGroups:
        - 20 # device group (often dialout)
    ```

5.  Use a minimal security context (least privilege first):

    Update `spec.template.spec.containers[0].securityContext`:

    ```yaml
    securityContext:
      runAsUser: 0
      runAsGroup: 0
      allowPrivilegeEscalation: false
    ```

If you still get permission errors, try adding capabilities. Only use `privileged: true` as a last resort.

### BLE

BLE is difficult to run in Kubernetes. Use TCP or serial whenever possible. If you must use BLE, expect additional host access and security considerations:

- Host networking and node pinning are typically required for stable BLE access.
- You may need access to the host Bluetooth stack (BlueZ) via DBus and elevated permissions.
- Start with the least privilege that works; only use privileged mode as a last resort.

Because environments differ widely, treat BLE support in Kubernetes as experimental.

## Notes

- Ready file: The ready file feature is enabled by default via `MMRELAY_READY_FILE=/run/mmrelay/ready` in the deployment:
  - Readiness and startup probes check for the marker file at `/run/mmrelay/ready`
  - Liveness probe runs `mmrelay doctor` for deeper health checks
  - Heartbeat interval is configurable via `MMRELAY_READY_HEARTBEAT_SECONDS` (default: 60s)
- NetworkPolicy: The default NetworkPolicy allows all egress; restrict CIDRs as needed for production. The default policy includes rules for both IPv4 (`0.0.0.0/0`) and IPv6 (`::/0`) egress.
