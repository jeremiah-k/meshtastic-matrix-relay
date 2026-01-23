# Kubernetes Deployment Guide

This guide explains how to deploy MMRelay on Kubernetes. The goal is a simple, repeatable setup that works on most clusters.

## Prerequisites

- Kubernetes cluster (v1.20+)
- `kubectl` configured to access your cluster
- MMRelay installed locally (for generating manifests): `pipx install mmrelay`

## Quick Start

```bash
# Generate Kubernetes manifests interactively
mmrelay k8s generate-manifests

# Edit the ConfigMap with your configuration
nano k8s/mmrelay-configmap.yaml

# If you chose to generate a Secret manifest, edit it now
# (file name depends on auth method)
#   k8s/mmrelay-secret-credentials.yaml
#   k8s/mmrelay-secret-matrix-credentials.yaml

# Deploy to your cluster
kubectl apply -f k8s/

# Check status
kubectl get pods -l app=mmrelay
kubectl logs -f deployment/mmrelay
```

## Authentication

Choose one method:

### Method 1: Environment variables (recommended for Kubernetes)

Create a Secret with the three required variables:

```bash
read -s -p "Matrix password: " MMRELAY_MATRIX_PASSWORD; echo
kubectl create secret generic mmrelay-matrix-credentials \
  --from-literal=MMRELAY_MATRIX_HOMESERVER=https://matrix.example.org \
  --from-literal=MMRELAY_MATRIX_BOT_USER_ID=@bot:example.org \
  --from-literal=MMRELAY_MATRIX_PASSWORD=$MMRELAY_MATRIX_PASSWORD
```

If you chose to generate the Secret manifest, update the file and apply it instead:

```bash
nano k8s/mmrelay-secret-matrix-credentials.yaml
kubectl apply -f k8s/mmrelay-secret-matrix-credentials.yaml
```

### Method 2: Credentials file (from `mmrelay auth login`)

1. Run `mmrelay auth login` locally to generate `credentials.json`.
2. Create the Secret from that file:

```bash
kubectl create secret generic mmrelay-credentials-json \
  --from-file=credentials.json=$HOME/.mmrelay/credentials.json
```

Or update and apply the generated Secret manifest:

```bash
nano k8s/mmrelay-secret-credentials.yaml
kubectl apply -f k8s/mmrelay-secret-credentials.yaml
```

## Storage and Persistence

MMRelay stores database, logs, E2EE keys, and plugin data under `/app/data`.

- The generator will show detected StorageClasses (when available).
- If a default StorageClass exists, it will be used as the suggested default.
- You can list StorageClasses manually with:

```bash
kubectl get storageclass
```

If you are unsure, accept the default and adjust later.

## Connection Types

### TCP (recommended)

In `config.yaml`:

```yaml
meshtastic:
  connection_type: tcp
  host: meshtastic.local # or IP address
  port: 4403
```

No special deployment configuration needed.

### Serial

Requires the Meshtastic device to be attached to a specific node.

1. Add the device mount and volume:

```yaml
volumeMounts:
  - name: serial-device
    mountPath: /dev/ttyUSB0
volumes:
  - name: serial-device
    hostPath:
      path: /dev/ttyUSB0
      type: CharDevice
```

2. Pin the pod to the node with the device:

```yaml
nodeSelector:
  kubernetes.io/hostname: node-with-device
```

3. Use a minimal security context:

```yaml
securityContext:
  runAsUser: 0
  runAsGroup: 0
  supplementalGroups:
    - 20 # device group (often dialout)
  allowPrivilegeEscalation: false
```

If you still get permission errors, try adding capabilities first:

```yaml
securityContext:
  allowPrivilegeEscalation: false
  capabilities:
    add: ["MKNOD", "SYS_ADMIN"]
```

Use `privileged: true` only as a last resort.

### BLE (not recommended)

BLE is difficult to run in Kubernetes. Use TCP or serial whenever possible.

If you must use BLE:

- Requires Linux nodes with Bluetooth hardware
- Usually requires `hostNetwork: true`
- Often requires privileged mode; capability-only setups are cluster dependent

Start with privileged and host networking only if your cluster policy allows it:

```yaml
spec:
  hostNetwork: true
  containers:
    - name: mmrelay
      securityContext:
        privileged: true
```

## Troubleshooting

```bash
kubectl get pods -l app=mmrelay
kubectl describe pod -l app=mmrelay
kubectl logs -f deployment/mmrelay
kubectl logs -l app=mmrelay --previous
```

Validate config inside the pod:

```bash
kubectl exec -it deployment/mmrelay -- mmrelay config check
```
