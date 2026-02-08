# Helm Chart Deployment Guide

This guide covers deploying MMRelay using the Helm chart in `deploy/helm/mmrelay/`.

> **Note**: The Helm chart is a thin renderer of the static manifests in `deploy/k8s/`. It does **not** change the runtime model. Both use MMRELAY_HOME=/data, a single persistent root at `/data`, config mounted at `/data/config.yaml`, and the same probes/lifecycle behavior.

## Prerequisites

- Kubernetes cluster (v1.20+)
- Helm 3.x
- `kubectl` configured for your cluster

## Upgrading to 1.3

If you are upgrading from 1.2.x or earlier, read and follow
`docs/MIGRATION_1.3.md` before deploying or upgrading the Helm release.
New installations can proceed with the Quick Start below.

## Quick Start

### 1. Get the chart (git checkout)

```bash
git clone https://github.com/jeremiah-k/meshtastic-matrix-relay.git mmrelay
cd mmrelay
```

### 2. Create config.yaml

```bash
curl -Lo config.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample_config.yaml
# Edit config.yaml with your settings
${EDITOR:-vi} config.yaml
```

### 3. Create the namespace

```bash
kubectl create namespace mmrelay
```

### 4. Create config Secret

```bash
kubectl create secret generic mmrelay-config \
  --from-file=config.yaml=./config.yaml \
  --namespace mmrelay
```

### 5. Install Chart

```bash
helm install mmrelay ./deploy/helm/mmrelay \
  --namespace mmrelay \
  --create-namespace
```

### 6. Verify Deployment

```bash
kubectl get pods -n mmrelay -l app.kubernetes.io/name=mmrelay
kubectl logs -n mmrelay -l app.kubernetes.io/name=mmrelay -f
```

## Configuration

The Helm chart is highly configurable. Key options include:

### Image Configuration

```yaml
image:
  repository: ghcr.io/jeremiah-k/mmrelay
  tag: 1.3.0
  pullPolicy: IfNotPresent
  # Pin by digest for production (immutable)
  digest: ""
  # Image pull secrets for private registries
  pullSecrets: []
```

**Pin by tag:**

```bash
helm upgrade mmrelay ./deploy/helm/mmrelay \
  --set image.tag=1.3.0 \
  --namespace mmrelay
```

**Pin by digest (recommended for production):**

```bash
helm upgrade mmrelay ./deploy/helm/mmrelay \
  --set image.digest=sha256:abc123... \
  --set image.tag= \
  --namespace mmrelay
```

> **Note**: Tags can be mutable. For production, pin a specific tag or digest.

### Config Injection

The chart supports two patterns for injecting `config.yaml`:

#### Pattern A: Secret (Default)

```yaml
config:
  enabled: true
  source: secret
  name: mmrelay-config
  key: config.yaml
  create: false
  data: ""
```

Create the Secret externally (recommended default):

```bash
kubectl create secret generic mmrelay-config \
  --from-file=config.yaml=./config.yaml \
  --namespace mmrelay
```

#### Pattern B: ConfigMap

```yaml
config:
  enabled: true
  source: configmap
  name: mmrelay-config
  key: config.yaml
  create: false
  data: ""
```

Create the ConfigMap externally:

```bash
kubectl create configmap mmrelay-config \
  --from-file=config.yaml=./config.yaml \
  --namespace mmrelay
```

**Important**: Only enable one pattern at a time (Secret OR ConfigMap), not both.
**Required**: `config.enabled` must remain `true` (the chart fails if disabled).

#### Optional: Helm-created config (explicit opt-in)

To have Helm create the Secret/ConfigMap, you must set `create: true` and provide inline `data`. If `data` is empty, the chart will fail with a clear error to prevent deploying invalid resources.

```yaml
config:
  enabled: true
  source: secret
  name: mmrelay-config
  key: config.yaml
  create: true
  data: |
    # inline config.yaml contents
    matrix:
      homeserver: https://matrix.org
      bot_user_id: "@bot:matrix.org"
```

### Credentials Injection

MMRelay can use a pre-created `credentials.json` from a Secret (recommended for credential rotation):

```yaml
credentials:
  enabled: true
  secretName: mmrelay-credentials
  key: credentials.json
  create: false
  data: ""
```

Create the credentials Secret:

```bash
kubectl create secret generic mmrelay-credentials \
  --from-file=credentials.json=./credentials.json \
  --namespace mmrelay
```

#### Optional: Helm-created credentials (explicit opt-in)

```yaml
credentials:
  enabled: true
  secretName: mmrelay-credentials
  key: credentials.json
  create: true
  data: |
    { "homeserver": "https://matrix.org", "user_id": "@bot:matrix.org", "access_token": "..." }
```

#### Bootstrap Mode (Alternative)

For initial deployment without existing credentials, use Matrix auth environment variables:

```yaml
matrixAuth:
  enabled: true
  secretName: mmrelay-matrix-auth
```

Create the auth Secret:

```bash
kubectl create secret generic mmrelay-matrix-auth \
  --from-literal=MMRELAY_MATRIX_HOMESERVER=$(read -p "Matrix homeserver URL: "; echo "$REPLY") \
  --from-literal=MMRELAY_MATRIX_BOT_USER_ID=$(read -p "Matrix bot user ID: "; echo "$REPLY") \
  --from-literal=MMRELAY_MATRIX_PASSWORD=$(read -s -p "Matrix password: "; echo >&2; echo "$REPLY") \
  --namespace mmrelay
```

On first startup, MMRelay will:

1. Read Matrix credentials from environment variables
2. Log into Matrix
3. Create `/data/matrix/credentials.json` on the PVC
4. Use existing `credentials.json` on subsequent restarts

### Persistence

```yaml
persistence:
  enabled: true
  # Use existing PVC
  existingClaim: ""
  # Create new PVC with these settings
  storageClass: ""
  size: 1Gi
  accessModes:
    - ReadWriteOnce
```

If `persistence.enabled=false`, the chart uses `emptyDir` for `/data`. **All state is ephemeral and will be lost on restart.** The initContainer chown step is automatically disabled in this mode.

**Use existing PVC:**

```bash
helm upgrade mmrelay ./deploy/helm/mmrelay \
  --set persistence.enabled=true \
  --set persistence.existingClaim=my-existing-pvc \
  --namespace mmrelay
```

**Create new PVC:**

```bash
helm upgrade mmrelay ./deploy/helm/mmrelay \
  --set persistence.enabled=true \
  --set persistence.storageClass=fast-ssd \
  --set persistence.size=5Gi \
  --namespace mmrelay
```

### Security Contexts

```yaml
podSecurityContext:
  fsGroup: 1000

containerSecurityContext:
  runAsUser: 1000
  runAsGroup: 1000
  runAsNonRoot: true
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop:
      - ALL
```

The chart uses a restrictive security posture by default. Only modify if required by specific features (e.g., serial device access).

### Health Probes

```yaml
probes:
  enabled: true
  readiness:
    initialDelaySeconds: 0
    periodSeconds: 10
    timeoutSeconds: 2
    failureThreshold: 3
  startup:
    initialDelaySeconds: 0
    periodSeconds: 5
    timeoutSeconds: 2
    failureThreshold: 60
  liveness:
    initialDelaySeconds: 0
    periodSeconds: 60
    timeoutSeconds: 20
    failureThreshold: 3
```

- **Readiness**: Checks for ready file at `/run/mmrelay/ready` (cheap, fast)
- **Startup**: Allows up to 5 minutes for initialization (60 failures × 5s = 300s)
- **Liveness**: Runs `mmrelay doctor` for deeper health checks

### Lifecycle Hooks

```yaml
lifecycle:
  enabled: true
  preStopSleepSeconds: 5
  terminationGracePeriodSeconds: 30
```

The preStop hook allows for a graceful shutdown period:

```bash
sleep 5
```

### Resources

```yaml
resources:
  requests:
    cpu: 50m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 512Mi
```

Adjust based on your workload:

```bash
helm upgrade mmrelay ./deploy/helm/mmrelay \
  --set resources.requests.cpu=100m \
  --set resources.limits.memory=1Gi \
  --namespace mmrelay
```

### Node Placement

```yaml
nodeSelector:
  node.kubernetes.io/instance-type: t3.medium

tolerations:
  - key: "workload"
    operator: "Equal"
    value: "mmrelay"
    effect: "NoSchedule"

affinity:
  podAntiAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchExpressions:
            - key: app.kubernetes.io/name
              operator: In
              values:
                - mmrelay
        topologyKey: "kubernetes.io/hostname"
```

### Service (Optional)

```yaml
service:
  enabled: false
  type: ClusterIP
  port: 8080
```

MMRelay has no stable port by default. Enable service only if you have configured MMRelay to expose a port (e.g., for plugins).

### ServiceAccount

```yaml
serviceAccount:
  create: false
  annotations: {}
  name: ""
```

### Ingress (Optional)

```yaml
ingress:
  enabled: false
  className: ""
  annotations: {}
  hosts:
    - host: mmrelay.example.com
      paths:
        - path: /
          pathType: Prefix
  tls: []
```

### Network Policy (Optional)

The chart can render a NetworkPolicy matching `deploy/k8s/networkpolicy.yaml`. It
denies all ingress and allows all egress by default.

```yaml
networkPolicy:
  enabled: true
```

### Horizontal Pod Autoscaler

```yaml
autoscaling:
  enabled: false
  minReplicas: 1
  maxReplicas: 3
  targetCPUUtilizationPercentage: 80
  targetMemoryUtilizationPercentage: 80
```

> **Note**: MMRelay is stateful. Enable autoscaling only if you have configured state externalization (e.g., external database).

## Runtime Paths

The chart uses fixed runtime paths (consistent with container design):

- **MMRELAY_HOME**: `/data` (PVC mount)
- **Config Path**: `/data/config.yaml` (from Secret/ConfigMap)
- **Ready File**: `/run/mmrelay/ready` (for probes)
- **Tmp**: `/tmp` (emptyDir)
- **Run**: `/run/mmrelay` (emptyDir)

All persistent data lives under `/data`:

- `/data/matrix/credentials.json` - Matrix authentication
- `/data/database/meshtastic.sqlite` - SQLite database
- `/data/logs/` - Application logs
- `/data/matrix/store/` - E2EE encryption keys (if enabled)
- `/data/plugins/custom/` - Custom plugins
- `/data/plugins/community/` - Community plugins

## Running Doctor

Verify your deployment inside the pod:

```bash
POD_NAME=$(kubectl get pods -n mmrelay -l app.kubernetes.io/name=mmrelay -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n mmrelay $POD_NAME -- mmrelay doctor
```

Expected output:

- HOME is `/data`
- No legacy environment variables
- All paths resolve under `/data`
- Database is accessible
- Credentials exist or can be created

## Upgrading

### Upgrade to 1.3.x

If upgrading from 1.2.x or earlier, follow `docs/MIGRATION_1.3.md` first.

```bash
helm upgrade mmrelay ./deploy/helm/mmrelay \
  --namespace mmrelay \
  --set image.tag=1.3.0
```

### Rolling Restart

```bash
kubectl rollout restart deployment/mmrelay -n mmrelay
```

### Backup Before Upgrade

#### Understanding Data Locations

All MMRelay persistent data lives under `/data` inside the container:

```text
/data/
├── matrix/
│   ├── credentials.json   # Matrix authentication credentials
│   └── store/             # E2EE encryption keys (if enabled)
├── database/
│   └── meshtastic.sqlite  # SQLite database (nodes, messages, state)
├── logs/                  # Application logs
└── plugins/               # Custom and community plugins
    ├── custom/
    └── community/
```

The Helm chart stores all data in a PVC mounted at `/data`. Backing up this PVC preserves your complete MMRelay state.

#### Backup Methods

##### Method 1: PVC snapshot (recommended)

If your Kubernetes storage provider supports volume snapshots:

```bash
# Create a snapshot of the mmrelay-data PVC
kubectl create volumesnapshot mmrelay-data-backup-$(date +%Y%m%d) \
  --source=persistentvolumeclaim/mmrelay-data \
  --namespace mmrelay
```

Check your cloud provider's documentation for snapshot creation limits, retention policies, and restoration procedures.

##### Method 2: Exec-based backup (quick and simple)

```bash
# Get the pod name
POD_NAME=$(kubectl get pods -n mmrelay -l app.kubernetes.io/name=mmrelay -o jsonpath='{.items[0].metadata.name}')

# Create a compressed backup
kubectl exec -n mmrelay $POD_NAME -- tar czf - /data > mmrelay-backup-$(date +%Y%m%d).tar.gz
```

##### Method 3: rsync backup (for larger deployments)

```bash
# Create a temporary pod with the PVC mounted
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
kubectl cp -n mmrelay backup-pod:/data ./mmrelay-backup-$(date +%Y%m%d)

# Clean up the temporary pod
kubectl delete pod backup-pod -n mmrelay
```

For detailed restore procedures and disaster recovery guidance, see the [KUBERNETES.md](KUBERNETES.md#backup-restore-and-disaster-recovery) document.

## Troubleshooting

### Pod Not Ready

```bash
kubectl describe pod -n mmrelay <pod-name>
kubectl logs -n mmrelay <pod-name>
```

Check:

- Config Secret/ConfigMap exists and is mounted
- PVC is bound and accessible
- Security context allows writing to `/data`
- InitContainer completed successfully

### Ready File Missing

```bash
kubectl exec -n mmrelay <pod-name> -- ls -l /run/mmrelay
kubectl logs -n mmrelay <pod-name> --tail=50
```

### Credentials Issues

```bash
kubectl exec -n mmrelay <pod-name> -- ls -l /data/matrix/credentials.json
kubectl exec -n mmrelay <pod-name> -- cat /data/config.yaml
```

If credentials don't exist, enable bootstrap mode or provide credentials via Secret.

### Database Issues

```bash
kubectl exec -n mmrelay <pod-name> -- ls -l /data/database/meshtastic.sqlite
kubectl exec -n mmrelay <pod-name> -- mmrelay doctor
```

### View Rendered Manifests

Debug Helm template rendering:

```bash
helm template mmrelay ./deploy/helm/mmrelay \
  --namespace mmrelay \
  > rendered.yaml
```

## Advanced Examples

### Serial Device Connection

Add serial device access with node pinning:

```yaml
extraVolumes:
  - name: serial-device
    hostPath:
      path: /dev/ttyUSB0
      type: CharDevice

extraVolumeMounts:
  - name: serial-device
    mountPath: /dev/ttyUSB0

nodeSelector:
  kubernetes.io/hostname: node-with-device

podSecurityContext:
  supplementalGroups:
    - 20
```

### External Secret Management

Use External Secrets Operator or Sealed Secrets:

```yaml
config:
  enabled: true
  source: secret
  name: mmrelay-config-external

credentials:
  enabled: true
  secretName: mmrelay-credentials-external
```

External secret manager creates Secrets with these names.

## Values Reference

For a complete list of configurable values, see `values.yaml` in the chart directory.

```bash
helm show values ./deploy/helm/mmrelay
```

## Migration from Static Manifests

If upgrading from `deploy/k8s/` static manifests to the Helm chart:

1. Backup your PVC: `kubectl exec -n mmrelay <pod> -- tar czf - /data > backup.tar.gz`
2. Install Helm chart with existing PVC:
   ```bash
   helm install mmrelay ./deploy/helm/mmrelay \
     --set persistence.existingClaim=mmrelay-data \
     --namespace mmrelay
   ```
3. Delete static manifests: `kubectl delete -f deploy/k8s/`

The Helm chart uses the same PVC names, resources, and configuration as the static manifests.

## Security Considerations

- **Image Pinning**: Pin by digest for production to ensure immutability
- **Secrets**: Use external secret managers (External Secrets, Sealed Secrets, Vault)
- **Network Policy**: Optional; enable with `networkPolicy.enabled=true` (disabled by default)
- **Pod Security**: Security context is restrictive; only relax if required
- **RBAC**: No RBAC resources included by default (no cluster-wide access needed)

## Comparison: Helm vs Static Manifests

| Feature            | Helm Chart                  | Static Manifests (deploy/k8s/) |
| ------------------ | --------------------------- | ------------------------------ |
| Configurability    | High (values.yaml)          | Manual (edit manifests)        |
| Version Management | `helm upgrade`              | `kubectl apply -k`             |
| Secret Management  | Flexible (Secret/ConfigMap) | Manual                         |
| PVC Management     | Dynamic or existing         | Manual                         |
| Probes & Lifecycle | Configurable                | Fixed                          |
| Recommended For    | Production, multi-env       | Simple deployments, debugging  |

Both implement the same container deployment model: MMRELAY_HOME=/data, PVC at /data, initContainer, probes, and lifecycle hooks.

## Support

For issues or questions:

- [GitHub Issues](https://github.com/jeremiah-k/meshtastic-matrix-relay/issues)
- [Matrix Room](https://matrix.to/#/#mmrelay:matrix.org)
- [Documentation](https://github.com/jeremiah-k/meshtastic-matrix-relay/blob/main/docs/)
