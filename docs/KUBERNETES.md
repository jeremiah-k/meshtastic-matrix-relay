# Kubernetes Deployment

Official Kubernetes support for MMRelay with persistent storage and health monitoring.

## Prerequisites

- Kubernetes cluster (v1.19+)
- `kubectl` configured to access your cluster
- StorageClass available for Persistent Volume Claims
- Meshtastic device accessible from cluster (TCP) or node (Serial/BLE)

## Quick Start

### Step 1: Apply All Resources

```bash
cd k8s/
kubectl apply -f .
```

This creates:

- ConfigMap with sample configuration
- Persistent Volume Claim for data storage
- Deployment with health checks

### Step 2: Configure MMRelay

Edit the ConfigMap:

```bash
kubectl edit configmap mmrelay-config
```

Update these fields:

- `matrix.homeserver` - Your Matrix homeserver URL
- `matrix.bot_user_id` - Your Matrix bot user ID
- `meshtastic.host` - Your Meshtastic device hostname/IP

### Step 3: Restart to Apply Changes

```bash
kubectl rollout restart deployment/mmrelay
```

### Step 4: Verify Deployment

```bash
# Check pod is running
kubectl get pods -l app=mmrelay

# View logs
kubectl logs -f deployment/mmrelay
```

## Authentication Methods

MMRelay supports three authentication approaches for Kubernetes:

### Method 1: Password in ConfigMap (Easiest)

Edit `k8s/configmap.yaml` before applying:

```yaml
matrix:
  homeserver: https://matrix.example.org
  bot_user_id: "@bot:example.matrix.org
  password: your_secure_password_here  # Add this line
```

After first successful startup:

1. MMRelay logs in to Matrix using the password
2. Creates `credentials.json` automatically in the PVC
3. You can remove the password from ConfigMap for security

**Pros:** Simple, no external tools needed
**Cons:** Password stored in ConfigMap (remove after first run)

### Method 2: Secret for Password (More Secure)

```bash
# 1. Create Secret from file
echo -n "your_password" | kubectl create secret generic mmrelay-matrix-password \
  --from-file=matrix-password=/dev/stdin

# 2. Use deployment-with-secret.yaml
kubectl apply -f deployment-with-secret.yaml
```

The deployment reads `MMRELAY_MATRIX_PASSWORD` from the Secret and automatically creates `credentials.json`.

**Pros:** Password stored in Kubernetes Secret (more secure)
**Cons:** Requires Secret creation step

### Method 3: External Auth (Most Secure)

```bash
# 1. Run auth locally on your machine
mmrelay auth login

# 2. Copy credentials.json to pod
kubectl cp ~/.mmrelay/credentials.json \
  $(kubectl get pod -l app=mmrelay -o jsonpath='{.items[0].metadata.name}'):/app/data/

# 3. Restart deployment
kubectl rollout restart deployment/mmrelay
```

**Pros:** No credentials stored in Kubernetes at all
**Cons:** Requires external machine with mmrelay installed

## Understanding Paths in Kubernetes

MMRelay container expects specific paths:

| Path               | Purpose              | Source           |
| ------------------ | -------------------- | ---------------- |
| `/app/config.yaml` | Configuration file   | ConfigMap        |
| `/app/data`        | Data directory (PVC) | PersistentVolume |

**Important:** The Docker image CMD already sets `--config /app/config.yaml` and `--data-dir /app/data`, so you don't need to worry about these flags.

### What's Stored in the PVC

The Persistent Volume Claim (`mmrelay-data`) contains:

```text
/app/data/
├── credentials.json          # Matrix authentication credentials (auto-created)
├── store/                   # E2EE encryption keys
├── data/
│   └── meshtastic.sqlite # Message database
└── logs/
    └── mmrelay.log      # Application logs
```

## Configuration Options

### ConfigMap Variables

The `k8s/configmap.yaml` file contains all MMRelay settings. Edit it before applying:

```yaml
matrix:
  homeserver: https://matrix.example.org
  bot_user_id: "@bot:example.matrix.org
  password: optional_password_here

matrix_rooms:
  - id: "#room:example.matrix.org"
    meshtastic_channel: 0

meshtastic:
  connection_type: tcp
  host: meshtastic.local
  port: 4403
  meshnet_name: Your Meshnet Name
  broadcast_enabled: true
  message_delay: 2.2

logging:
  level: info
  log_to_file: true
  filename: /app/logs/mmrelay.log
```

### Environment Variable Overrides

You can override ConfigMap settings with environment variables:

```bash
kubectl set env deployment/mmrelay MMRELAY_MESHTASTIC_HOST=meshtastic.local
kubectl set env deployment/mmrelay MMRELAY_LOGGING_LEVEL=debug
```

Available environment variables:

- `MMRELAY_MATRIX_PASSWORD` - Matrix password (from Secret)
- `MMRELAY_MESHTASTIC_CONNECTION_TYPE` - tcp, serial, or ble
- `MMRELAY_MESHTASTIC_HOST` - Meshtastic device hostname/IP
- `MMRELAY_MESHTASTIC_PORT` - Meshtastic TCP port (default: 4403)
- `MMRELAY_MESHTASTIC_MESHNET_NAME` - Mesh network name
- `MMRELAY_MESHTASTIC_BROADCAST_ENABLED` - Enable Matrix→Mesh messages (true/false)
- `MMRELAY_MESHTASTIC_MESSAGE_DELAY` - Delay between mesh messages (seconds, min: 2.0)
- `MMRELAY_LOGGING_LEVEL` - debug, info, warning, error, critical
- `MMRELAY_LOG_FILE` - Custom log file path
- `MMRELAY_DATABASE_PATH` - Custom database path

## Storage Configuration

### Default PVC (1Gi)

The default `k8s/pvc.yaml` requests 1Gi of storage:

```yaml
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
  storageClassName: standard
```

### Using Different Storage Class

Edit `k8s/pvc.yaml` to match your cluster's storage:

```yaml
spec:
  storageClassName: fast-ssd # Change this
  resources:
    requests:
      storage: 5Gi # Adjust size
```

Common storage classes:

- `standard` - Standard block storage
- `gp2` - AWS EBS
- `standard-rwo` - Google Cloud Persistent Disk
- `fast-ssd` - Custom SSD storage class (cluster-specific)

### Storage Sizing

- **Minimum**: 100Mi (basic usage)
- **Recommended**: 1Gi (includes E2EE keys and database growth)
- **Large deployment**: 5Gi+ (large message history, many plugins)

## Health Monitoring

The deployment includes health checks:

### Liveness Probe

```yaml
livenessProbe:
  exec:
    command: ["pgrep", "-f", "mmrelay"]
  initialDelaySeconds: 30
  periodSeconds: 10
  timeoutSeconds: 5
  failureThreshold: 3
```

Checks if mmrelay process is running every 10 seconds. After 3 consecutive failures, the pod is restarted.

### Readiness Probe

```yaml
readinessProbe:
  exec:
    command: ["pgrep", "-f", "mmrelay"]
  initialDelaySeconds: 10
  periodSeconds: 5
  timeoutSeconds: 3
  failureThreshold: 3
```

Pod is marked as "ready" only when mmrelay process is running. Traffic is not sent to the pod until it's ready.

## Resource Limits

The default deployment specifies these resources:

```yaml
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi
```

### Adjusting Resources

Edit `k8s/deployment.yaml`:

```yaml
resources:
  requests:
    cpu: 200m # Increase for busy meshes
    memory: 512Mi
  limits:
    cpu: 1000m # More CPU for faster processing
    memory: 1Gi # More memory for large message history
```

### Resource Sizing Guide

| Mesh Size            | CPU Request | CPU Limit | Memory Request | Memory Limit |
| -------------------- | ----------- | --------- | -------------- | ------------ |
| Small (1-10 nodes)   | 100m        | 500m      | 256Mi          | 512Mi        |
| Medium (10-50 nodes) | 200m        | 1000m     | 512Mi          | 1Gi          |
| Large (50+ nodes)    | 500m        | 2000m     | 1Gi            | 2Gi          |

## Device Connection Types

### TCP Connection (Recommended)

No additional Kubernetes configuration needed. MMRelay initiates outbound connection to Meshtastic device.

```yaml
meshtastic:
  connection_type: tcp
  host: meshtastic.local # Device must be reachable from cluster
  port: 4403
```

**Network consideration:** Ensure Meshtastic device is accessible from your cluster (same network, or via VPN, or public IP).

### Serial Connection

Requires device pass-through to pod:

```yaml
# Edit k8s/deployment.yaml
spec:
  template:
    spec:
      hostDevices:
        - name: ttyUSB0
          path: /dev/ttyUSB0
          group: 20 # dialout group on many systems
```

And in ConfigMap:

```yaml
meshtastic:
  connection_type: serial
  serial_port: /dev/ttyUSB0
```

**Note:** Serial device must be on the same node as the pod. Use nodeSelector to schedule on specific node.

### BLE Connection

Requires host networking and additional privileges:

```yaml
# Edit k8s/deployment.yaml
spec:
  template:
    spec:
      hostNetwork: true
      securityContext:
        capabilities:
          add: ["NET_ADMIN", "NET_RAW"]
```

And in ConfigMap:

```yaml
meshtastic:
  connection_type: ble
  ble_address: "AA:BB:CC:DD:EE:FF"
```

**Important:** BLE only works on Linux hosts. Not supported on managed Kubernetes services that don't allow host networking.

## Managing the Deployment

### View Status

```bash
# Get deployment status
kubectl get deployment mmrelay

# Get pod details
kubectl describe pod -l app=mmrelay

# View real-time logs
kubectl logs -f deployment/mmrelay
```

### Update Configuration

```bash
# Edit ConfigMap
kubectl edit configmap mmrelay-config

# Restart to apply
kubectl rollout restart deployment/mmrelay

# Watch rollout status
kubectl rollout status deployment/mmrelay
```

### Scale Deployment

**Important:** Do not scale beyond 1 replica. MMRelay uses a single Matrix device ID, and multiple instances would conflict.

```bash
# View current replicas
kubectl get deployment mmrelay

# Reset to 1 replica (if accidentally scaled)
kubectl scale deployment mmrelay --replicas=1
```

### Rolling Update

```bash
# Update to new image version
kubectl set image deployment/mmrelay mmrelay=ghcr.io/jeremiah-k/mmrelay:v1.2.1

# Watch rolling update
kubectl rollout status deployment/mmrelay
```

### Access Pod Shell

```bash
# Get interactive shell
kubectl exec -it deployment/mmrelay -- /bin/bash

# List data directory
kubectl exec deployment/mmrelay -- ls -la /app/data

# View credentials.json (if exists)
kubectl exec deployment/mmrelay -- cat /app/data/credentials.json
```

## Troubleshooting

### Pod Won't Start

```bash
# Describe pod to see events
kubectl describe pod -l app=mmrelay

# Check pod logs
kubectl logs deployment/mmrelay --previous  # Previous instance if crashed
```

Common issues:

- **Image pull error**: Check image name and registry access
- **ConfigMap not found**: Verify ConfigMap exists (`kubectl get configmap`)
- **PVC pending**: Check StorageClass and available storage
- **Permission denied**: Container runs as user ID 1000, verify PVC permissions

### ConfigMap Not Applied

```bash
# Verify ConfigMap content
kubectl get configmap mmrelay-config -o yaml

# Check if mounted correctly
kubectl describe pod -l app=mmrelay | grep -A10 Mounts
```

### Credentials Not Persisting

```bash
# Check PVC is bound
kubectl get pvc

# Verify PVC is mounted
kubectl exec deployment/mmrelay -- df -h | grep /app/data

# Check if credentials.json exists
kubectl exec deployment/mmrelay -- test -f /app/data/credentials.json && echo "Found" || echo "Not found"
```

### Connection Issues

**TCP:**

- Verify Meshtastic device is reachable from cluster
- Check firewall rules
- Test connectivity: `kubectl exec deployment/mmrelay -- ping meshtastic.local`

**Serial:**

- Verify device is on the same node
- Check device permissions
- View available devices: `kubectl exec deployment/mmrelay -- ls -la /dev/tty*`

**BLE:**

- Ensure host networking is enabled
- Verify Bluetooth adapter is available on node
- Check capabilities: `kubectl exec deployment/mmrelay -- getcap -v`

### Permission Errors

Add security context to fix permission issues:

```yaml
spec:
  template:
    spec:
      securityContext:
        fsGroup: 1000
      containers:
        - name: mmrelay
          securityContext:
            runAsUser: 1000
```

### High Memory Usage

```bash
# Check resource usage
kubectl top pod -l app=mmrelay

# Increase limits if needed
kubectl set resources deployment/mmrelay --limits=memory=1Gi
```

Common causes:

- Large message history in database
- Memory leak in plugins
- Enable debug logging temporarily to diagnose

## Backup and Recovery

### Backup PVC Data

```bash
# Create a pod with PVC mounted for backup
kubectl run backup-pod --image=busybox --overrides='
{
  "spec": {
    "containers": [{
      "name": "backup",
      "image": "busybox",
      "command": ["tar", "czf", "-", "/app/data"],
      "volumeMounts": [{
        "name": "data",
        "mountPath": "/app/data"
      }]
    }],
    "volumes": [{
      "name": "data",
      "persistentVolumeClaim": {
        "claimName": "mmrelay-data"
      }
    }]
  }
}' --rm -i --restart=Never > backup.tar.gz
```

### Restore from Backup

```bash
# Restore backup to PVC
kubectl run restore-pod --image=busybox --overrides='
{
  "spec": {
    "containers": [{
      "name": "restore",
      "image": "busybox",
      "command": ["tar", "xzf", "-", "-C", "/app"],
      "stdin": true,
      "volumeMounts": [{
        "name": "data",
        "mountPath": "/app/data"
      }]
    }],
    "volumes": [{
      "name": "data",
      "persistentVolumeClaim": {
        "claimName": "mmrelay-data"
      }
    }]
  }
}' -i --restart=Never < backup.tar.gz
```

## Uninstalling

```bash
# Delete all resources
kubectl delete -f k8s/

# Or individually
kubectl delete deployment mmrelay
kubectl delete configmap mmrelay-config
kubectl delete pvc mmrelay-data
kubectl delete secret mmrelay-matrix-password  # If using Secret
```

**Warning:** Deleting the PVC will delete all persisted data (credentials.json, database, E2EE keys). Backup before deletion.

## Advanced Topics

### Node Selector for Serial/BLE

Schedule on specific node for device access:

```yaml
spec:
  template:
    spec:
      nodeSelector:
        kubernetes.io/hostname: node-with-device
```

### Tolerations for Tainted Nodes

Run on dedicated or master node:

```yaml
spec:
  template:
    spec:
      tolerations:
        - key: node-role.kubernetes.io/master
          effect: NoSchedule
```

### Custom Service (Optional)

Not required for TCP connections, but useful for node port access:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: mmrelay-service
spec:
  selector:
    app: mmrelay
  ports:
    - port: 4403
      targetPort: 4403
      protocol: TCP
  type: LoadBalancer # Or NodePort
```

## Security Considerations

- **Single Instance**: Never scale replicas beyond 1
- **Secrets**: Use Kubernetes Secrets for sensitive data, not ConfigMaps
- **Network Policies**: Configure network policies to restrict pod communication
- **RBAC**: Create service accounts with minimal permissions if needed
- **TLS**: Use TLS for Meshtastic TCP connections when possible
- **E2EE**: Enable Matrix E2EE for encrypted room participation

## Next Steps

- See [Advanced Configuration](ADVANCED_CONFIGURATION.md) for detailed configuration options
- See [Docker Guide](DOCKER.md) for Docker-specific deployment tips
- See [E2EE Guide](E2EE.md) for encryption setup
- Join [#mmrelay:matrix.org](https://matrix.to/#/#mmrelay:matrix.org) for help
