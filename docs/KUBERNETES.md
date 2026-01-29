# Kubernetes Deployment Guide

> **Note**: Kubernetes deployment is supported via static manifests. No generator is required.

This guide uses the static manifests in `deploy/k8s/`. Copy them into your deployment repo, create a Secret with your `config.yaml`, then apply.

## Prerequisites

- Kubernetes cluster (v1.20+)
- `kubectl` (includes kustomize support for `kubectl apply -k`)

## Quick Start (static manifests)

```bash
# Copy the static manifests into your deployment repo
cp -R deploy/k8s ./mmrelay-k8s

# Ensure the namespace exists
kubectl create namespace mmrelay --dry-run=client -o yaml | kubectl apply -f -

# Edit namespace/image tag in kustomization.yaml if desired
$EDITOR ./mmrelay-k8s/kustomization.yaml
# If you change the namespace above, update the --namespace/-n flags below to match
# for secret creation and kubectl apply/get/log commands.

# Create config.yaml from the project sample
curl -Lo ./config.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/src/mmrelay/tools/sample_config.yaml
$EDITOR ./config.yaml

# Recommended: set a writable credentials path in config.yaml for the container
# matrix:
#   credentials_path: /app/data/credentials.json
#   e2ee:
#     store_path: /app/data/store

# The default manifest already sets MMRELAY_CREDENTIALS_PATH=/app/data/credentials.json
# so credentials will persist on the PVC even if you leave this commented out.

# Create a Matrix auth secret (environment-based auth)
kubectl create secret generic mmrelay-matrix-auth \
  --from-literal=MMRELAY_MATRIX_HOMESERVER=https://matrix.example.org \
  --from-literal=MMRELAY_MATRIX_BOT_USER_ID=@bot:example.org \
  --from-literal=MMRELAY_MATRIX_PASSWORD=your_password \
  --namespace mmrelay

# Store config.yaml in a Kubernetes Secret
kubectl create secret generic mmrelay-config \
  --from-file=config.yaml=./config.yaml \
  --namespace mmrelay

# Apply manifests
kubectl apply -k ./mmrelay-k8s

# Check status
kubectl get pods -n mmrelay -l app=mmrelay
kubectl logs -n mmrelay -f deployment/mmrelay
```

## Optional: pin the image digest

If you want immutable image references, use the digest overlay. Replace the
placeholder digest in `deploy/k8s/overlays/digest/kustomization.yaml`, then:

```bash
kubectl apply -k ./mmrelay-k8s/overlays/digest
```

## Secrets and configuration

The deployment mounts a Secret named `mmrelay-config` with one key:

- `config.yaml`

Authentication secrets are provided separately using environment variables
via the optional `mmrelay-matrix-auth` Secret (see example above). On first
startup, MMRelay will log in with the provided credentials and create
`/app/data/credentials.json` on the persistent volume.

This keeps sensitive data out of the manifests so you can publish the manifests without exposing secrets. If you use an external secrets manager (External Secrets, Sealed Secrets, Vault, etc.), create the same Secret name/keys.

## Storage

`deploy/k8s/pvc.yaml` uses the cluster default StorageClass. If your cluster requires a specific StorageClass, add `storageClassName` there.

## Connection types

### TCP (recommended)

No manifest changes required. Configure `meshtastic.connection_type: tcp` in `config.yaml`.

### Serial

Serial requires host device access and node pinning. Start with the most restrictive settings and only escalate if needed.

```yaml
# 1) Mount the device
volumes:
  - name: serial-device
    hostPath:
      path: /dev/ttyUSB0
volumeMounts:
  - name: serial-device
    mountPath: /dev/ttyUSB0
```

```yaml
# 2) Pin to the node with the device
nodeSelector:
  kubernetes.io/hostname: node-with-device
```

```yaml
# 3) Security context (least privilege first)
securityContext:
  runAsUser: 0
  runAsGroup: 0
  supplementalGroups:
    - 20 # device group (often dialout)
```

If you still get permission errors, try adding capabilities. Only use `privileged: true` as a last resort.

### BLE

BLE is difficult to run in Kubernetes. Use TCP or serial whenever possible.

## Notes

The default manifest sets `MMRELAY_CREDENTIALS_PATH=/app/data/credentials.json` so credentials created during first-run login persist on the PVC even when you authenticate via environment variables.
