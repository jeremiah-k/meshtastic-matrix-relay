# MMRelay on Kubernetes

Simple Kubernetes deployment for MMRelay with persistent storage for credentials and data.

## Quick Start

```bash
# 1. Generate ConfigMap from sample config (always up-to-date)
mmrelay k8s configmap > k8s-configmap.yaml

# 2. Edit ConfigMap to add your settings
nano k8s-configmap.yaml

# 3. Apply Kubernetes resources
kubectl apply -f k8s-configmap.yaml -f k8s/pvc.yaml -f k8s/deployment.yaml

# 4. Check deployment status
kubectl get pods -l app=mmrelay

# 5. View logs
kubectl logs -f deployment/mmrelay

# 6. Get shell access (for debugging)
kubectl exec -it deployment/mmrelay -- /bin/bash
```

## Files

- `deployment.yaml` - Main deployment
- `pvc.yaml` - Persistent volume for credentials.json and data

## How It Works

MMRelay uses these paths in the container:

- `/app/config.yaml` - Configuration file (from ConfigMap)
- `/app/data` - Data directory (from PVC)

**Important:** The Docker container is already configured to use these paths via CMD, so you don't need to worry about `--config` or `--data-dir` flags.

### What's Stored in the PVC

The Persistent Volume Claim stores:

- `credentials.json` - Matrix authentication credentials (auto-created on first run)
- `store/` - E2EE encryption keys
- `data/meshtastic.sqlite` - Message database
- `logs/` - Application logs

## Configuration

### Option 1: Edit ConfigMap Directly

```bash
# Edit the ConfigMap
kubectl edit configmap mmrelay-config

# This will reload on pod restart
kubectl rollout restart deployment/mmrelay
```

### Option 2: Use External Auth (Recommended for Production)

```bash
# 1. Generate ConfigMap and apply resources
mmrelay k8s configmap > k8s-configmap.yaml
# Edit if needed before applying:
kubectl apply -f k8s-configmap.yaml -f k8s/pvc.yaml -f k8s/deployment.yaml

# 2. Get credentials.json from your local system
# Run mmrelay auth login locally, then copy credentials.json

# 3. Copy credentials.json to PVC
# Note: Ensure only one pod is running before this command
kubectl cp ~/.mmrelay/credentials.json $(kubectl get pod -l app=mmrelay -o jsonpath='{.items[0].metadata.name}'):/app/data/

# 4. Restart pod to load credentials
kubectl rollout restart deployment/mmrelay
```

## Matrix Authentication Methods

MMRelay supports two authentication approaches in Kubernetes:

### 1. Secret for Password (Recommended)

- Generate Secret: `mmrelay k8s secret > k8s-secret.yaml`
- Edit the password field and apply: `kubectl apply -f k8s-secret.yaml`
- Edit `k8s/deployment.yaml` to add Secret environment variable (see below)
- MMRelay automatically creates credentials.json on first run

To wire the Secret to your deployment, add this to the container spec in `deployment.yaml`:

```yaml
env:
  - name: MMRELAY_MATRIX_PASSWORD
    valueFrom:
      secretKeyRef:
        name: mmrelay-matrix-password
        key: MMRELAY_MATRIX_PASSWORD
```

### 2. External Auth (Most Secure)

- Run `mmrelay auth login` locally on your machine
- Copy `~/.mmrelay/credentials.json` to the PVC
- No password stored anywhere in Kubernetes

## Customizing for Your Cluster

### Change Storage Class

By default, the PVC uses your cluster's default storage class for maximum portability. To specify a custom class, edit `k8s/pvc.yaml`:

```yaml
spec:
  storageClassName: fast-ssd # Your storage class (omit to use cluster default)
  resources:
    requests:
      storage: 1Gi # Adjust size
```

**Note:** Omitting `storageClassName` uses the cluster's default StorageClass. If your cluster doesn't have a default configured, you must specify a class explicitly.

### Adjust Resource Limits

Edit the `resources` section in `deployment.yaml`:

```yaml
resources:
  requests:
    cpu: 200m # Increase for busy meshes
    memory: 512Mi
  limits:
    cpu: 1000m
    memory: 1Gi
```

### Serial/BLE Device Pass-through

For serial connections, add device pass-through:

```yaml
spec:
  template:
    spec:
      volumes:
        - name: usb-devices
          hostPath:
            path: /dev/ttyUSB0
            type: CharDevice
      containers:
        - name: mmrelay
          # ... other config ...
          volumeMounts:
            - name: usb-devices
              mountPath: /dev/ttyUSB0
```

For BLE, enable host networking (requires privileges):

```yaml
spec:
  template:
    spec:
      hostNetwork: true
      containers:
        - name: mmrelay
          securityContext:
            capabilities:
              add: ["NET_ADMIN", "NET_RAW"]
```

## Health Checks

The deployment includes health probes that check if the mmrelay process is running:

- **Liveness Probe** - Restarts the pod if process crashes
- **Readiness Probe** - Marks pod as ready only when process is running

Both use `pgrep -f mmrelay` to verify the process.

**Note:** These are basic health checks that verify the process is running. They do not check if the application is actually healthy or can connect to external services (Matrix, Meshtastic). For more sophisticated monitoring, consider implementing an HTTP health endpoint or custom probe scripts.

## Troubleshooting

### Pod Won't Start

```bash
# Check pod status
kubectl describe pod -l app=mmrelay

# View logs
kubectl logs -f deployment/mmrelay
```

### ConfigMap Not Applied

```bash
# Verify ConfigMap exists
kubectl get configmap mmrelay-config

# Check config content
kubectl get configmap mmrelay-config -o yaml
```

### Credentials Not Persisting

```bash
# Check PVC is bound
kubectl get pvc

# Verify PVC is mounted
kubectl describe pod -l app=mmrelay | grep -A5 Mounts
```

### Permission Errors

The container runs as user ID 1000 (mmrelay). Ensure the PVC is writable:

```bash
# Check pod logs for permission errors
kubectl logs deployment/mmrelay | grep -i permission

# If needed, add security context to deployment
spec:
  template:
    spec:
      securityContext:
        fsGroup: 1000
```

## Updating Configuration

After editing ConfigMap, restart the deployment:

```bash
kubectl rollout restart deployment/mmrelay
```

Or, for a rolling update without downtime:

```bash
kubectl rollout restart deployment/mmrelay
```

## Removing the Deployment

```bash
# Delete MMRelay resources by name (recommended)
kubectl delete deployment mmrelay
kubectl delete configmap mmrelay-config
kubectl delete pvc mmrelay-data
kubectl delete secret mmrelay-matrix-password  # If using Secret

# Or delete specific files if you have only MMRelay manifests in k8s/
kubectl delete -f k8s/deployment.yaml
kubectl delete -f k8s/pvc.yaml
kubectl delete -f k8s-configmap.yaml
kubectl delete -f k8s-secret.yaml  # If using Secret
```

## Important Notes

- **Single Instance Only**: MMRelay uses a single Matrix device ID. Do not scale replicas beyond 1.
- **Persistent Storage**: Required for credentials.json. Without PVC, credentials are lost on pod restart.
- **Security**: Remove Matrix passwords from ConfigMap after first successful startup.
- **E2EE**: End-to-End Encryption keys are stored in `/app/data/store/` in the PVC.
- **Database**: The SQLite database at `/app/data/data/meshtastic.sqlite` stores message mappings and will grow over time.

## Advanced: Using Environment Variables

For advanced users, you can override ConfigMap settings with environment variables:

```bash
kubectl set env deployment/mmrelay MMRELAY_MESHTASTIC_HOST=meshtastic.local
kubectl set env deployment/mmrelay MMRELAY_LOGGING_LEVEL=debug
```

Available environment variables:

- `MMRELAY_MESHTASTIC_CONNECTION_TYPE` - tcp, serial, or ble
- `MMRELAY_MESHTASTIC_HOST` - Meshtastic device hostname/IP
- `MMRELAY_MESHTASTIC_PORT` - Meshtastic TCP port
- `MMRELAY_MESHTASTIC_MESHNET_NAME` - Mesh network name
- `MMRELAY_LOGGING_LEVEL` - debug, info, warning, error, critical
- `MMRELAY_DATABASE_PATH` - Custom database path

See [Advanced Configuration](../docs/ADVANCED_CONFIGURATION.md) for details.
