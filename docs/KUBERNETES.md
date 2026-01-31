# Kubernetes Deployment Guide

> **Note**: Kubernetes deployment is currently in testing and development. We welcome feedback to help improve the manifests and deployment experience.

This guide uses the static manifests in `deploy/k8s/`. Download them, create a Secret with your `config.yaml`, then apply.

## Prerequisites

- Kubernetes cluster (v1.20+)
- `kubectl` (includes kustomize support for `kubectl apply -k`)

## Version Selection

The `MMRELAY_VERSION` variable is the container image tag (e.g., `1.2.9`, `latest`). This is used for:

- Setting the image tag in kustomization.yaml
- Downloading the sample config.yaml

The Kubernetes manifests are always downloaded from the `main` branch since they have not yet been included in a release.

- **Production**: Use a specific release tag (e.g., `1.2.9`) for stability and reproducibility.
- **Development**: Use `latest` for the latest development changes.
- **Digest overlay**: Requires a release tag with published image digests. The overlay is only available for production releases.

**Important**: The manifests from `main` branch work with any existing image tag (`1.2.9`, `latest`, etc.), even though the release didn't include these manifests.

## Quick Start (static manifests)

```bash
# Create a project directory and change into it
mkdir -p mmrelay
cd mmrelay

# Set the version for the container image tag
export MMRELAY_VERSION=1.2.9

# Download manifests from the main branch
BASE_URL="https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/main/deploy/k8s"
for manifest in pvc.yaml networkpolicy.yaml deployment.yaml kustomization.yaml overlays/digest/kustomization.yaml; do
  DEST_PATH="./mmrelay-k8s/${manifest}"
  mkdir -p "$(dirname "${DEST_PATH}")"
  curl -Lo "${DEST_PATH}" "${BASE_URL}/${manifest}"
done

# Ensure the namespace exists
kubectl create namespace mmrelay --dry-run=client -o yaml | kubectl apply -f -

# Edit namespace/image tag in kustomization.yaml
$EDITOR ./mmrelay-k8s/kustomization.yaml
# Set the image newTag to match your MMRELAY_VERSION (${MMRELAY_VERSION})
# If you change the namespace above, update the --namespace/-n flags below to match
# for secret creation and kubectl apply/get/log commands.

# Create config.yaml from the project sample (downloaded from the same version as your image)
curl -Lo ./config.yaml https://raw.githubusercontent.com/jeremiah-k/meshtastic-matrix-relay/${MMRELAY_VERSION}/src/mmrelay/tools/sample_config.yaml
$EDITOR ./config.yaml

# The default manifest sets MMRELAY_CREDENTIALS_PATH=/data/credentials.json,
# so credentials will persist on the PVC. You can override this by explicitly
# setting it in config.yaml:
# matrix:
#   credentials_path: /data/credentials.json
#   e2ee:
#     store_path: /data/store

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
kubectl apply -k ./mmrelay-k8s

# Check status
kubectl get pods -n mmrelay -l app=mmrelay
kubectl logs -n mmrelay -f deployment/mmrelay
```

## Optional: pin the image digest

If you want immutable image references for production, use the digest overlay.
This requires a published image with a digest from GitHub Packages.

Replace the placeholder digest in `./mmrelay-k8s/overlays/digest/kustomization.yaml`.
Tags and digests are listed on the GitHub Packages page:
[https://github.com/jeremiah-k/meshtastic-matrix-relay/pkgs/container/mmrelay](https://github.com/jeremiah-k/meshtastic-matrix-relay/pkgs/container/mmrelay)

```bash
kubectl apply -k ./mmrelay-k8s/overlays/digest
```

## Secrets and configuration

The deployment mounts a Secret named `mmrelay-config` with one key:

- `config.yaml`

Authentication secrets are provided separately using environment variables
via the optional `mmrelay-matrix-auth` Secret (see example above). On first
startup, MMRelay will log in with the provided credentials and create
`/data/credentials.json` on the persistent volume.

This keeps sensitive data out of the manifests so you can publish the manifests without exposing secrets. If you use an external secrets manager (External Secrets, Sealed Secrets, Vault, etc.), create the same Secret name/keys.

## Storage

The deployment uses `/data` as the base directory for all persistent data:

- **Credentials**: `/data/credentials.json` (auto-created on first login)
- **Logs**: `/data/logs/mmrelay.log`
- **Database**: `/data/mmrelay.db`
- **E2EE store**: `/data/store/` (if encryption is enabled)

This is configured in `deployment.yaml` via `--base-dir /data` and the PVC mount. All data persists across pod restarts.

`./mmrelay-k8s/pvc.yaml` uses the cluster default StorageClass. If your cluster requires a specific StorageClass, add `storageClassName` there.

## Connection types

### TCP (recommended)

No manifest changes required. Configure `meshtastic.connection_type: tcp` in `config.yaml`.

### Serial

Serial requires host device access and node pinning. Start with the most restrictive settings and only escalate if needed.

1.  Add the device mount to the container:

    In `./mmrelay-k8s/deployment.yaml`, add this entry under
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

- Credentials path: The default manifest sets `MMRELAY_CREDENTIALS_PATH=/data/credentials.json` so credentials created during first-run login persist on the PVC even when you authenticate via environment variables.
- Ready file: The ready file feature is opt-in and enabled via `MMRELAY_READY_FILE=/run/mmrelay/ready`. When enabled:
  - Readiness/liveness probes check for the marker file at `/run/mmrelay/ready`
  - Heartbeat interval is configurable via `MMRELAY_READY_HEARTBEAT_SECONDS` (default: 60s)
  - **Important**: If you increase `MMRELAY_READY_HEARTBEAT_SECONDS` above 120s, update the liveness probe threshold (120s) in the manifest to match
- NetworkPolicy: The default NetworkPolicy allows all egress; restrict CIDRs as needed for production. The default policy includes rules for both IPv4 (`0.0.0.0/0`) and IPv6 (`::/0`) egress.
