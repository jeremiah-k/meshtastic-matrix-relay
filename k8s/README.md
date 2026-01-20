# MMRelay on Kubernetes

Simple Kubernetes deployment for MMRelay with persistent storage for credentials and data.

## Quick Start

```bash
# 1. Generate ConfigMap from sample config (always up-to-date)
mmrelay k8s generate configmap > k8s-configmap.yaml

# 2. Edit ConfigMap to add your settings
nano k8s-configmap.yaml

# 3. Apply Kubernetes resources
kubectl apply -f k8s/pvc.yaml -f k8s/configmap.yaml -f k8s/deployment.yaml

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

### Option 2: Use Matrix Password in ConfigMap

Edit `k8s/configmap.yaml` before applying:

```yaml
matrix:
  homeserver: https://matrix.example.org
  bot_user_id: "@bot:example.matrix.org
  password: your_password_here  # Set this
```

After first successful startup, MMRelay will:

1. Log in to Matrix using the password
2. Create `credentials.json` in the PVC
3. Remove the password from config for security

### Option 3: Use External Auth (Recommended for Production)

```bash
# 1. Apply deployment (without password in config)
kubectl apply -f k8s/pvc.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/configmap.yaml

# 2. Get credentials.json from your local system
# Run mmrelay auth login locally, then copy credentials.json

# 3. Copy credentials.json to PVC
kubectl cp ~/.mmrelay/credentials.json $(kubectl get pod -l app=mmrelay -o jsonpath='{.items[0].metadata.name}'):/app/data/

# 4. Restart pod to load credentials
kubectl rollout restart deployment/mmrelay
```

## Matrix Authentication Methods

MMRelay supports three authentication approaches in Kubernetes:

### 1. Password in ConfigMap (Easiest)

- Edit `configmap.yaml` and add your password
- MMRelay automatically creates credentials.json on first run
- Remove password from config after first startup for security

### 2. Secret for Password (More Secure)

- Create Secret: `kubectl apply -f k8s/secret.yaml.example`
- Use `deployment-with-secret.yaml` which reads from Secret
- Environment variable `MMRELAY_MATRIX_PASSWORD` is read from Secret
- MMRelay automatically creates credentials.json on first run

### 3. External Auth (Most Secure)

- Run `mmrelay auth login` locally on your machine
- Copy `~/.mmrelay/credentials.json` to the PVC
- No password stored anywhere in Kubernetes

## Customizing for Your Cluster

### Change Storage Class

Edit `k8s/pvc.yaml`:

```yaml
spec:
  storageClassName: fast-ssd # Your storage class
  resources:
    requests:
      storage: 1Gi # Adjust size
```

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
      containers:
        - name: mmrelay
          # ... other config ...
      hostDevices:
        - name: ttyUSB0
          path: /dev/ttyUSB0
```

For BLE, enable host networking (requires privileges):

```yaml
spec:
  template:
    spec:
      hostNetwork: true
      securityContext:
        capabilities:
          add: ["NET_ADMIN", "NET_RAW"]
```

## Health Checks

The deployment includes health probes that check if the mmrelay process is running:

- **Liveness Probe** - Restarts the pod if process crashes
- **Readiness Probe** - Marks pod as ready only when process is running

Both use `pgrep -f mmrelay` to verify the process.

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
kubectl patch deployment mmrelay -p '{"spec":{"template":{"metadata":{"annotations":{"kubectl.kubernetes.io/restartedAt":"'$(date +%s)'"}}}}'
```

## Removing the Deployment

```bash
# Delete all resources
kubectl delete -f k8s/

# Or individually
kubectl delete deployment mmrelay
kubectl delete configmap mmrelay-config
kubectl delete pvc mmrelay-data
kubectl delete secret mmrelay-matrix-password  # If using Secret
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
