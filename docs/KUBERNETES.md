# Kubernetes Deployment Guide

This guide explains how to deploy MMRelay on Kubernetes. MMRelay officially supports Kubernetes as a deployment option alongside pipx and Docker.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Deployment Methods](#deployment-methods)
- [Configuration](#configuration)
- [Storage and Persistence](#storage-and-persistence)
- [Connection Types](#connection-types)
- [Monitoring and Troubleshooting](#monitoring-and-troubleshooting)
- [Advanced Configuration](#advanced-configuration)

## Prerequisites

- Kubernetes cluster (v1.20+)
- `kubectl` configured to access your cluster
- Basic understanding of Kubernetes concepts (Pods, Deployments, ConfigMaps, Secrets)
- MMRelay installed locally (for generating manifests): `pipx install mmrelay`

## Quick Start

The fastest way to deploy MMRelay on Kubernetes is using the built-in manifest generator:

```bash
# Generate Kubernetes manifests interactively
mmrelay k8s generate-manifests

# This will create:
# - ./k8s/mmrelay-pvc.yaml (Persistent storage)
# - ./k8s/mmrelay-configmap.yaml (Generated from sample_config.yaml)
# - ./k8s/mmrelay-deployment.yaml (Application deployment)
# - ./k8s/mmrelay-secret-credentials.yaml (Optional: only if using credentials file auth)
```

The wizard will ask you about:

- Namespace
- Authentication method (environment variables or credentials file)
- Connection type (TCP or serial)
- Storage requirements

After generation:

```bash
# Review the generated files
ls -la ./k8s/

# Edit the ConfigMap with your actual configuration values
nano ./k8s/mmrelay-configmap.yaml

# If using environment variables (recommended), create the Matrix credentials secret:
# Use read -s to securely enter password without storing in shell history
read -s -p "Enter Matrix password: " MMRELAY_MATRIX_PASSWORD && echo
kubectl create secret generic mmrelay-matrix-credentials \
  --from-literal=MMRELAY_MATRIX_HOMESERVER=https://matrix.example.org \
  --from-literal=MMRELAY_MATRIX_BOT_USER_ID=@bot:example.org \
  --from-literal=MMRELAY_MATRIX_PASSWORD="$MMRELAY_MATRIX_PASSWORD" \
  --namespace=default  # Replace with the namespace chosen during manifest generation

# If using credentials file authentication, apply the generated secret file instead:
# (Uncomment and run this command instead of the kubectl create secret command above)
# kubectl apply -f ./k8s/mmrelay-secret-credentials.yaml
```

2. The deployment reads these environment variables to authenticate. If `credentials.json` does not already exist, it will be automatically generated on first startup.

3. E2EE support is automatically enabled when credentials are created this way (Linux containers only).

**Security Note:** After the first successful startup, the password is only needed if `credentials.json` is lost or corrupted. Consider using Kubernetes secret rotation policies.

### Method 2: Credentials File (Advanced)

This method uses a pre-generated `credentials.json` file from `mmrelay auth login`. This is useful for advanced scenarios where you need full control over device identity or E2EE setup.

**Advantages:**

- Full control over device identity and E2EE keys
- Pre-verified E2EE setup
- Useful for migrating existing installations

**Setup:**

1. Run `mmrelay auth login` locally to generate credentials:

```bash
mmrelay auth login
# Follow prompts to authenticate
# This creates ~/.mmrelay/credentials.json
```

2. Create a Kubernetes Secret from the credentials file:

```bash
kubectl create secret generic mmrelay-credentials-json \
  --from-file=credentials.json=$HOME/.mmrelay/credentials.json \
  --namespace=default  # Replace with the namespace chosen during manifest generation
```

3. When generating manifests, choose "Credentials file" as the authentication method. This will generate the appropriate secret template and deployment configuration.

4. The deployment will mount this secret at `/app/data/credentials.json`.

**Note:** This method requires running `mmrelay auth login` locally before deploying to Kubernetes.

## Deployment Methods

### Automated (Recommended)

Use the built-in wizard:

```bash
mmrelay k8s generate-manifests
```

### Manual

If you prefer to create manifests manually or customize extensively:

1. **Generate a sample config:**

```bash
mmrelay config generate --output config.yaml
```

2. **Edit the config with your settings**

3. **Create Kubernetes resources manually:**

```bash
# Create namespace (optional: use 'default' namespace instead)
kubectl create namespace mmrelay  # Replace with your desired namespace, or omit to use 'default'

# Create PersistentVolumeClaim
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: mmrelay-data
  namespace: mmrelay  # Replace with your chosen namespace
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
EOF

# Create ConfigMap from your config file
kubectl create configmap mmrelay-config \
  --from-file=config.yaml=config.yaml \
  --namespace=mmrelay  # Replace with your chosen namespace

# Create Secret for Matrix credentials (choose one method from above)
# Method 1: Environment variables
# Use read -s to securely enter password without storing in shell history
read -s -p "Enter Matrix password: " MMRELAY_MATRIX_PASSWORD && echo
kubectl create secret generic mmrelay-matrix-credentials \
  --from-literal=MMRELAY_MATRIX_HOMESERVER=https://matrix.example.org \
  --from-literal=MMRELAY_MATRIX_BOT_USER_ID=@bot:example.org \
  --from-literal=MMRELAY_MATRIX_PASSWORD="$MMRELAY_MATRIX_PASSWORD" \
  --namespace=mmrelay  # Replace with the namespace chosen during manifest generation

# Apply the deployment (use generated or create your own)
kubectl apply -f mmrelay-deployment.yaml
```

## Configuration

### ConfigMap Structure

The ConfigMap is generated from MMRelay's `sample_config.yaml` and contains your `config.yaml`. The generated ConfigMap includes all available configuration options with sensible defaults.

**When using environment variable authentication** (recommended), you can leave the Matrix password field empty in the ConfigMap, as credentials will be provided via the Kubernetes Secret:

```yaml
matrix:
  homeserver: https://matrix.example.org
  bot_user_id: "@bot:example.org"
  # Password provided by MMRELAY_MATRIX_PASSWORD environment variable from Secret

matrix_rooms:
  - id: "#room:example.org"
    meshtastic_channel: 0

meshtastic:
  connection_type: tcp
  host: meshtastic.local
  meshnet_name: My Meshnet
  broadcast_enabled: true

logging:
  level: info

plugins:
  ping:
    active: true
```

**When using credentials file authentication**, no Matrix credentials are needed in the ConfigMap at all, since `credentials.json` is mounted from a Secret:

```yaml
matrix:
  # All credentials provided by /app/data/credentials.json

matrix_rooms:
  - id: "#room:example.org"
    meshtastic_channel: 0

meshtastic:
  connection_type: tcp
  host: meshtastic.local
  meshnet_name: My Meshnet
  broadcast_enabled: true
```

### Environment Variable Overrides

You can override any configuration value using environment variables:

| Environment Variable                 | Configuration Path           | Example                      |
| ------------------------------------ | ---------------------------- | ---------------------------- |
| `MMRELAY_MATRIX_HOMESERVER`          | `matrix.homeserver`          | `https://matrix.example.org` |
| `MMRELAY_MATRIX_BOT_USER_ID`         | `matrix.bot_user_id`         | `@bot:matrix.example.org`    |
| `MMRELAY_MATRIX_PASSWORD`            | `matrix.password`            | `secret_password`            |
| `MMRELAY_MESHTASTIC_HOST`            | `meshtastic.host`            | `192.168.1.100`              |
| `MMRELAY_MESHTASTIC_CONNECTION_TYPE` | `meshtastic.connection_type` | `tcp`                        |
| `MMRELAY_LOGGING_LEVEL`              | `logging.level`              | `debug`                      |
| `MMRELAY_DATABASE_PATH`              | `database.path`              | `/app/data/custom.db`        |

Add these to your deployment's `env` section or use `envFrom` with a Secret.

## Storage and Persistence

MMRelay requires persistent storage for:

- Database (message history, node information)
- Logs
- E2EE encryption keys and store (if E2EE is enabled)
- Plugin data

### PersistentVolumeClaim

The generated manifests create a PVC with these defaults:

- Access mode: `ReadWriteOnce`
- Storage class: `standard` (customize during generation)
- Size: `1Gi` (customize during generation)

### Volume Mount Structure

```text
/app/data/
├── credentials.json (if using auth login method)
├── data/
│   └── meshtastic.sqlite (database)
├── logs/
│   └── mmrelay.log
├── store/ (E2EE keys, if enabled)
└── plugins/
```

### Storage Class Selection

During manifest generation, you can specify your storage class:

- `standard` - Default, works on most clusters
- `gp2` / `gp3` - AWS EBS
- `pd-standard` / `pd-ssd` - Google Cloud
- `azure-disk` - Azure
- Custom storage class name from your cluster

## Connection Types

### TCP Connection (Recommended)

Easiest to configure in Kubernetes. Your Meshtastic device needs to be network-accessible.

**ConfigMap:**

```yaml
meshtastic:
  connection_type: tcp
  host: meshtastic.local # or IP address
  port: 4403
```

**No special deployment configuration needed** - MMRelay makes an outbound connection.

### Serial Connection

Requires the Meshtastic device to be connected to a specific node.

**ConfigMap:**

```yaml
meshtastic:
  connection_type: serial
  serial_port: /dev/ttyUSB0
```

**Deployment additions:**

1. Add device mount and volume definition to the pod spec:

```yaml
spec:
  containers:
    # ... existing container config ...
    volumeMounts:
      # ... existing mounts ...
      - name: serial-device
        mountPath: /dev/ttyUSB0
  volumes:
    # ... existing volumes ...
    - name: serial-device
      hostPath:
        path: /dev/ttyUSB0
        type: CharDevice
```

2. Add node selector to ensure pod runs on the correct node:

```yaml
nodeSelector:
  kubernetes.io/hostname: node-with-device
```

3. Add security context for device access (prefer scoped permissions):

```yaml
securityContext:
  runAsUser: 0
  runAsGroup: 0
  supplementalGroups:
    - 20 # Replace with the device group ID (often "dialout")
  allowPrivilegeEscalation: false
```

**Security Warning:** Running containers as root (runAsUser: 0) increases the attack surface and should only be used when necessary for device access. Prefer scoped permissions or specific capabilities when possible.

If the device still cannot be opened, you may need broader permissions depending on your cluster's security policy. Consider adding specific capabilities (for example `CAP_SYS_ADMIN` or `CAP_MKNOD`) or, as a last resort:

```yaml
securityContext:
  privileged: true
```

**Security Warning:** Running containers in privileged mode grants them all capabilities of the host machine, which is a significant security risk. This should only be used when absolutely necessary for device access.

### BLE Connection

**Not recommended for Kubernetes** due to complexity of Bluetooth device access in containers. Use TCP or serial instead.

If you must use BLE:

- Requires `hostNetwork: true`
- Requires privileged mode
- Only works on Linux nodes
- Node must have Bluetooth adapter
- See Docker documentation for additional requirements

## Monitoring and Troubleshooting

### Check Pod Status

```bash
# Get pod status
kubectl get pods -l app=mmrelay

# Describe pod for events
kubectl describe pod -l app=mmrelay

# View logs
kubectl logs -f deploy/mmrelay

# View previous logs if pod restarted
kubectl logs -l app=mmrelay --previous
```

### Common Issues

**Pod is CrashLoopBackOff:**

```bash
# Check logs for errors
kubectl logs -l app=mmrelay --tail=50

# Common causes:
# 1. Invalid configuration
# 2. Missing Matrix credentials
# 3. Cannot connect to Meshtastic device
# 4. Storage permissions issues
```

**Configuration validation:**

```bash
# Exec into pod to validate config
kubectl exec -it deployment/mmrelay -- mmrelay config check
```

**Authentication issues:**

```bash
# Check if credentials.json was created
kubectl exec -it deployment/mmrelay -- ls -la /app/data/

# Check Matrix authentication
kubectl exec -it deployment/mmrelay -- mmrelay auth status
```

**Storage issues:**

```bash
# Check PVC status
kubectl get pvc mmrelay-data

# Check PV status
kubectl get pv

# Verify volume mount
kubectl exec -it deployment/mmrelay -- df -h /app/data
```

**Meshtastic connection issues:**

```bash
# For TCP connections, test connectivity
kubectl exec -it deployment/mmrelay -- ping meshtastic.local

# Check if port is accessible
kubectl exec -it deployment/mmrelay -- nc -zv meshtastic.local 4403

# View Meshtastic-related logs
kubectl logs -f deployment/mmrelay | grep -i meshtastic
```

### Health Checks

The generated deployment includes liveness and readiness probes:

```yaml
livenessProbe:
  exec:
    command: ["pgrep", "-f", "mmrelay"]
  initialDelaySeconds: 30
  periodSeconds: 30

readinessProbe:
  exec:
    command: ["pgrep", "-f", "mmrelay"]
  initialDelaySeconds: 10
  periodSeconds: 10
```

**Note:** These probes use `pgrep` to check if the process is running. This is a basic health check that only verifies process existence. For production deployments, consider implementing a dedicated HTTP health check endpoint within the application that verifies connectivity to Matrix and Meshtastic services before returning a healthy status.

Customize these based on your requirements.

### Resource Limits

Default resource requests and limits:

```yaml
resources:
  requests:
    memory: "256Mi"
    cpu: "100m"
  limits:
    memory: "512Mi"
    cpu: "500m"
```

Adjust based on your usage patterns and cluster resources.

## Advanced Configuration

### Using External Secrets

For production deployments, consider using an external secrets manager:

#### AWS Secrets Manager

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: mmrelay-matrix-credentials
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secrets-manager
    kind: SecretStore
  target:
    name: mmrelay-matrix-credentials
  data:
    - secretKey: MMRELAY_MATRIX_HOMESERVER
      remoteRef:
        key: mmrelay/matrix
        property: homeserver
    - secretKey: MMRELAY_MATRIX_BOT_USER_ID
      remoteRef:
        key: mmrelay/matrix
        property: bot_user_id
    - secretKey: MMRELAY_MATRIX_PASSWORD
      remoteRef:
        key: mmrelay/matrix
        property: password
```

#### HashiCorp Vault

```yaml
apiVersion: secrets-store.csi.x-k8s.io/v1
kind: SecretProviderClass
metadata:
  name: mmrelay-vault-secrets
spec:
  provider: vault
  parameters:
    vaultAddress: "https://vault.example.com"
    roleName: "mmrelay"
    objects: |
      - objectName: "homeserver"
        secretPath: "secret/data/mmrelay/matrix"
        secretKey: "homeserver"
      - objectName: "bot_user_id"
        secretPath: "secret/data/mmrelay/matrix"
        secretKey: "bot_user_id"
      - objectName: "password"
        secretPath: "secret/data/mmrelay/matrix"
        secretKey: "password"
  secretObjects:
    - secretName: mmrelay-matrix-credentials
      type: Opaque
      data:
        - objectName: homeserver
          key: MMRELAY_MATRIX_HOMESERVER
        - objectName: bot_user_id
          key: MMRELAY_MATRIX_BOT_USER_ID
        - objectName: password
          key: MMRELAY_MATRIX_PASSWORD
```

### Scaling

MMRelay currently does not support horizontal scaling (multiple replicas) because:

1. Single Meshtastic device connection per instance
2. SQLite database is single-writer
3. Stateful E2EE session

Keep `replicas: 1` in your deployment.

For high availability:

- Use pod anti-affinity to place replicas on different nodes
- Ensure PVC can be remounted quickly during node failures
- Consider using a StatefulSet instead of Deployment for better persistent volume handling

### Network Policies

Example network policy to restrict MMRelay traffic:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: mmrelay-netpol
spec:
  podSelector:
    matchLabels:
      app: mmrelay
  policyTypes:
    - Egress
  egress:
    # Allow DNS
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    # Allow Matrix homeserver (external). Replace with your homeserver IP/CIDR.
    - to:
        - ipBlock:
            cidr: 203.0.113.10/32
      ports:
        - protocol: TCP
          port: 443
        - protocol: TCP
          port: 8448
    # Allow Meshtastic device (LAN). Replace with your device IP/CIDR.
    - to:
        - ipBlock:
            cidr: 192.168.1.50/32
      ports:
        - protocol: TCP
          port: 4403
```

Notes:

- Update the DNS labels to match your cluster (CoreDNS labels can vary).
- For external services, NetworkPolicy can only match IPs/CIDRs; if you need
  hostname-based rules, use an egress gateway or proxy.
- If your Matrix homeserver runs inside the cluster, replace the `ipBlock`
  with namespace/pod selectors for that service.

### Init Containers

For advanced setup, you might want an init container:

```yaml
initContainers:
  - name: setup
    image: ghcr.io/jeremiah-k/mmrelay:v1.3.0 # Pin to specific version for production
    command: ["sh", "-c"]
    args:
      - |
        # Validate configuration
        mmrelay config check --config /app/config.yaml

        # Pre-create directory structure
        mkdir -p /app/data/logs /app/data/data /app/data/store

        # Set permissions
        chmod 700 /app/data/store
    volumeMounts:
      - name: config
        mountPath: /app/config.yaml
        subPath: config.yaml
      - name: data
        mountPath: /app/data
```

### Updating MMRelay

To update to a new version:

```bash
# Update the deployment image
kubectl set image deployment/mmrelay \
  mmrelay=ghcr.io/jeremiah-k/mmrelay:<VERSION>  # Replace with desired version

# Or edit the deployment
kubectl edit deployment mmrelay

# Watch rollout status
kubectl rollout status deployment/mmrelay

# If issues, rollback
kubectl rollout undo deployment/mmrelay
```

For automatic updates, consider using tools like:

- [Renovate](https://github.com/renovatebot/renovate)
- [Flux](https://fluxcd.io/)
- [ArgoCD Image Updater](https://argocd-image-updater.readthedocs.io/)

## Migration from Docker

If you're migrating from Docker to Kubernetes:

1. **Export existing data:**

```bash
# On Docker host
docker cp meshtastic-matrix-relay:/app/data ./mmrelay-data
```

2. **Create ConfigMap from existing config:**

```bash
kubectl create configmap mmrelay-config \
  --from-file=config.yaml=./mmrelay-data/config.yaml
```

3. **Copy credentials if using auth login method:**

```bash
kubectl create secret generic mmrelay-credentials-json \
  --from-file=credentials.json=./mmrelay-data/credentials.json
```

4. **Copy database and E2EE store to PVC:**

```bash
# Create a temporary pod with PVC mounted
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: mmrelay-data-copy
spec:
  containers:
  - name: copier
    image: busybox
    command: ["sleep", "3600"]
    volumeMounts:
    - name: data
      mountPath: /data
  volumes:
  - name: data
    persistentVolumeClaim:
      claimName: mmrelay-data
EOF

# Wait for pod to be ready
kubectl wait --for=condition=Ready pod/mmrelay-data-copy

# Copy data
kubectl cp ./mmrelay-data/data mmrelay-data-copy:/data/
kubectl cp ./mmrelay-data/store mmrelay-data-copy:/data/
kubectl cp ./mmrelay-data/logs mmrelay-data-copy:/data/

# Clean up
kubectl delete pod mmrelay-data-copy
```

5. **Deploy MMRelay**:

```bash
kubectl apply -f ./k8s/
```

## Complete Example

Here's a complete example deploying MMRelay with environment variable authentication:

```bash
# 1. Generate manifests
mmrelay k8s generate-manifests
# Choose: namespace=default, auth=env, connection=tcp, storage=standard/1Gi

# 2. Create Matrix credentials secret
# Use read -s to securely enter password without storing in shell history
read -s -p "Enter Matrix password: " MMRELAY_MATRIX_PASSWORD && echo
kubectl create secret generic mmrelay-matrix-credentials \
  --from-literal=MMRELAY_MATRIX_HOMESERVER=https://matrix.example.org \
  --from-literal=MMRELAY_MATRIX_BOT_USER_ID=@mybot:matrix.example.org \
  --from-literal=MMRELAY_MATRIX_PASSWORD="$MMRELAY_MATRIX_PASSWORD"

# 3. Customize ConfigMap
nano k8s/mmrelay-configmap.yaml
# Update Matrix rooms, Meshtastic connection details, etc.

# 4. Apply all manifests
kubectl apply -f k8s/

# 5. Watch deployment
kubectl get pods -l app=mmrelay -w

# 6. Check logs
kubectl logs -f deploy/mmrelay
# or: kubectl logs -f -l app=mmrelay

# 7. Verify Matrix connection
kubectl exec -it deploy/mmrelay -- mmrelay auth status
```

That's it! Your MMRelay is now running on Kubernetes.

## Getting Help

- Check the main [README](../README.md) for general MMRelay information
- See [INSTRUCTIONS.md](INSTRUCTIONS.md) for configuration details
- See [E2EE.md](E2EE.md) for encryption setup
- Join our Matrix room: [#mmrelay:matrix.org](https://matrix.to/#/#mmrelay:matrix.org)
- Report issues: [GitHub Issues](https://github.com/jeremiah-k/meshtastic-matrix-relay/issues)
