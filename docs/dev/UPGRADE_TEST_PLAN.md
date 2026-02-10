# MMRelay Upgrade and Deployment Test Plan

## Overview

This document outlines a comprehensive test plan for validating MMRelay's upgrade path from v1.2.11 to the latest version, followed by Helm and Kubernetes deployment testing.

## Test Environment

| Component             | Value                  |
| --------------------- | ---------------------- |
| **Dev VM Host**       | `agent@192.168.1.190`  |
| **Kubernetes**        | microk8s               |
| **Matrix Homeserver** | `https://synod.im`     |
| **Matrix Bot User**   | `@mmrelaybot:synod.im` |
| **Target Image**      | (specified at runtime) |

## Prerequisites

The test agent will be provided with:

- **Development Ubuntu VM** at `agent@192.168.1.190` with Docker, Helm, and pipx installed
- **microk8s** cluster running on the VM
- **Config.yaml** configured for TCP-mode Meshtastic node with channel mappings
- **Sudo password** for the dev VM
- **Matrix bot password** for `@mmrelaybot:synod.im`
- **Target image tag** for upgrade testing (specified at runtime)

### Initial Connection

```bash
# SSH to the dev VM
ssh agent@192.168.1.190
# Password: [FILL IN]

# Verify environment
docker --version
helm version
microk8s kubectl get nodes
microk8s status
```

---

## Part 1: Docker Upgrade Test (v1.2.11 → Latest)

### 1.1 Initial Setup with v1.2.11

```bash
# Create directories following v1.2.x layout
mkdir -p ~/mmrelay/config ~/mmrelay/data ~/mmrelay/logs

# Create config.yaml (use provided config)
# Place at: ~/mmrelay/config/config.yaml

# Create .env file for v1.2.11
cat > ~/mmrelay/.env << 'EOF'
MMRELAY_HOME=/data
EOF

# Create docker-compose.yaml for v1.2.11 (old volume mount pattern)
cat > ~/mmrelay/docker-compose.yaml << 'EOF'
version: "3.8"
services:
  mmrelay:
    image: ghcr.io/jeremiah-k/mmrelay:1.2.11
    restart: unless-stopped
    volumes:
      - ./config:/app/config:ro
      - ./data:/app/data
      - ./logs:/app/logs
    env_file:
      - .env
EOF
```

### 1.2 Start v1.2.11 and Verify Operation

```bash
cd ~/mmrelay

# Start the container
docker compose up -d

# Check logs for successful startup
docker compose logs -f mmrelay

# Verify:
# - Meshtastic connection established
# - Matrix login successful
# - Messages flowing between platforms
# - Database created at expected location

# Document the v1.2.x file layout:
find ~/mmrelay -type f -exec ls -la {} \;
docker compose exec mmrelay ls -la /app/
docker compose exec mmrelay ls -la /app/data/ 2>/dev/null || echo "No /app/data"
```

### 1.3 Simulate Watchtower Auto-Upgrade

```bash
cd ~/mmrelay

# Option A: Direct image update (simulates watchtower)
docker compose down
sed -i 's|image: ghcr.io/jeremiah-k/mmrelay:1.2.11|image: ghcr.io/jeremiah-k/mmrelay:TARGET_TAG|' docker-compose.yaml
docker compose pull
docker compose up -d

# Option B: Actually use watchtower for realistic test
docker run -d \
  --name watchtower \
  -v /var/run/docker.sock:/var/run/docker.sock \
  containrrr/watchtower \
  --interval 30 mmrelay-mmrelay-1

# Wait for upgrade and monitor
docker compose logs -f mmrelay
```

### 1.4 Post-Upgrade Observation

```bash
# Check if container starts (even with warnings)
docker compose ps

# Capture all logs - look for:
# - Deprecation warnings
# - Migration suggestions
# - Errors
docker compose logs mmrelay 2>&1 | tee upgrade_logs.txt

# Check file system changes
find ~/mmrelay -type f -exec ls -la {} \; > post_upgrade_files.txt

# Verify functionality:
# - Can send messages Meshtastic → Matrix?
# - Can send messages Matrix → Meshtastic?
# - Database accessible?
# - Plugins working?
```

### 1.5 Test Migration Functionality

```bash
# Run migration with dry-run first
docker compose exec mmrelay mmrelay migrate --dry-run

# Document what would be migrated
docker compose exec mmrelay mmrelay migrate

# Verify migration
docker compose exec mmrelay mmrelay verify-migration

# Run doctor to check overall health
docker compose exec mmrelay mmrelay doctor

# Document file layout after migration
find ~/mmrelay -type f -exec ls -la {} \; > post_migration_files.txt
```

### 1.6 Update to v1.3+ Docker Compose Pattern

```bash
# Backup existing compose
cp docker-compose.yaml docker-compose.yaml.legacy.bak

# Update .env for new pattern
cat > .env << 'EOF'
# v1.3+ uses MMRELAY_HOST_HOME for the host path
MMRELAY_HOST_HOME=/home/$(whoami)/mmrelay-data
EOF

# Create new docker-compose.yaml (v1.3 pattern)
cat > docker-compose.yaml << 'EOF'
version: "3.8"
services:
  mmrelay:
    image: ghcr.io/jeremiah-k/mmrelay:TARGET_TAG
    restart: unless-stopped
    volumes:
      - ${MMRELAY_HOST_HOME}:/data
    environment:
      - TZ=UTC
EOF

# Create data directory
mkdir -p ~/mmrelay-data

# Restart with new pattern
docker compose down
docker compose up -d

# Verify operation
docker compose logs -f mmrelay
```

### 1.7 Document Findings

Record the following:

| Item                 | v1.2.11 | Post-Upgrade | Post-Migration | Notes |
| -------------------- | ------- | ------------ | -------------- | ----- |
| Container starts     |         |              |                |       |
| Meshtastic connected |         |              |                |       |
| Matrix authenticated |         |              |                |       |
| Messages relay       |         |              |                |       |
| Database location    |         |              |                |       |
| Config location      |         |              |                |       |
| Warnings observed    | N/A     |              |                |       |
| Errors observed      | N/A     |              |                |       |
| File permissions     |         |              |                |       |

---

## Part 2: Helm Chart Test (Latest Image Only)

### 2.1 Prerequisites

```bash
# Verify Helm is installed
helm version

# Verify kubectl access (kind, minikube, or k3s)
kubectl get nodes
```

### 2.2 Create Required Secrets

```bash
# Create namespace
microk8s kubectl create namespace mmrelay

# Create Matrix auth secret
# Password: [FILL IN]
microk8s kubectl create secret generic mmrelay-matrix-auth \
  --from-literal=MMRELAY_MATRIX_HOMESERVER='https://synod.im' \
  --from-literal=MMRELAY_MATRIX_BOT_USER_ID='@mmrelaybot:synod.im' \
  --from-literal=MMRELAY_MATRIX_PASSWORD='[FILL IN PASSWORD]' \
  --namespace mmrelay

# Create config secret (use provided config.yaml)
microk8s kubectl create secret generic mmrelay-config \
  --from-file=config.yaml=./config.yaml \
  --namespace mmrelay
```

### 2.3 Deploy with Helm

```bash
# Clone/copy the Helm chart locally
# Assuming chart is at: ./deploy/helm/mmrelay

# Install with default values (adjust as needed)
helm install mmrelay ./deploy/helm/mmrelay \
  --namespace mmrelay \
  --set image.tag=TARGET_TAG \
  --set matrixAuth.enabled=true \
  --set matrixAuth.secretName=mmrelay-matrix-auth \
  --set persistence.enabled=true

# Monitor rollout
microk8s kubectl rollout status deployment/mmrelay -n mmrelay -w

# Check pod status
microk8s kubectl get pods -n mmrelay

# Check init container logs
microk8s kubectl logs -n mmrelay deployment/mmrelay -c init-mmrelay

# Check main container logs
microk8s kubectl logs -n mmrelay deployment/mmrelay -c mmrelay -f
```

### 2.4 Verify Helm Deployment

```bash
# Check PVC was created
microk8s kubectl get pvc -n mmrelay

# Check secrets are mounted
microk8s kubectl describe pod -n mmrelay -l app.kubernetes.io/name=mmrelay

# Verify init container copied config
microk8s kubectl exec -n mmrelay deployment/mmrelay -- ls -la /data/

# Verify Matrix auth
microk8s kubectl exec -n mmrelay deployment/mmrelay -- mmrelay doctor

# Check ready file
microk8s kubectl exec -n mmrelay deployment/mmrelay -- cat /run/mmrelay/ready
```

### 2.5 Test Scenarios

1. **Cold start after PVC has data**: Delete pod, verify it restarts cleanly
2. **Config update**: Update secret, delete /data/config.yaml, restart pod
3. **Health probes**: Verify readiness/liveness probes work
4. **Message relay**: Verify bidirectional message flow

```bash
# Cold start test
microk8s kubectl delete pod -n mmrelay -l app.kubernetes.io/name=mmrelay
microk8s kubectl rollout status deployment/mmrelay -n mmrelay -w
microk8s kubectl logs -n mmrelay deployment/mmrelay -c mmrelay --tail=50

# Verify config exists on PVC
microk8s kubectl exec -n mmrelay deployment/mmrelay -- cat /data/config.yaml | head -20

# Verify credentials were generated
microk8s kubectl exec -n mmrelay deployment/mmrelay -- ls -la /data/matrix/
```

### 2.6 Document Helm Findings

| Item                        | Status | Notes |
| --------------------------- | ------ | ----- |
| Chart installs successfully |        |       |
| Init container runs         |        |       |
| Config copied to PVC        |        |       |
| Matrix auth works           |        |       |
| Meshtastic connects         |        |       |
| Messages relay              |        |       |
| Probes work                 |        |       |
| Pod restarts cleanly        |        |       |

---

## Part 3: Kubernetes Deployment Test (Latest Image Only)

### 3.1 Create Namespace and Secrets

```bash
# Create namespace
microk8s kubectl create namespace mmrelay

# Create secrets
# Password: [FILL IN]
microk8s kubectl create secret generic mmrelay-matrix-auth \
  --from-literal=MMRELAY_MATRIX_HOMESERVER='https://synod.im' \
  --from-literal=MMRELAY_MATRIX_BOT_USER_ID='@mmrelaybot:synod.im' \
  --from-literal=MMRELAY_MATRIX_PASSWORD='[FILL IN PASSWORD]' \
  --namespace mmrelay

microk8s kubectl create secret generic mmrelay-config \
  --from-file=config.yaml=./config.yaml \
  --namespace mmrelay
```

### 3.2 Apply K8s Manifests

```bash
# Apply PVC first
microk8s kubectl apply -f deploy/k8s/pvc.yaml -n mmrelay

# Apply deployment (update image tag first)
# Edit deploy/k8s/deployment.yaml to set image tag, or use kustomize
microk8s kubectl apply -f deploy/k8s/deployment.yaml -n mmrelay

# Monitor rollout
microk8s kubectl rollout status deployment/mmrelay -n mmrelay -w
```

### 3.3 Verify K8s Deployment

```bash
# Check pods
microk8s kubectl get pods -n mmrelay

# Check init container
microk8s kubectl logs -n mmrelay deployment/mmrelay -c init-mmrelay

# Check main container
microk8s kubectl logs -n mmrelay deployment/mmrelay -c mmrelay -f

# Run doctor
microk8s kubectl exec -n mmrelay deployment/mmrelay -- mmrelay doctor
```

### 3.4 Document K8s Findings

| Item                            | Status | Notes |
| ------------------------------- | ------ | ----- |
| Deployment applies successfully |        |       |
| PVC created                     |        |       |
| Init container runs             |        |       |
| Main container starts           |        |       |
| Matrix auth works               |        |       |
| Messages relay                  |        |       |

---

## Part 4: Comprehensive Report Template

### Executive Summary

- Overall upgrade success rate
- Breaking changes discovered
- Recommended actions for users

### Detailed Findings

#### Docker Upgrade (v1.2.11 → Latest)

1. **Initial State (v1.2.11)**
   - File locations:
   - Configuration:
   - Database:

2. **Post-Upgrade State**
   - Warnings:
   - Errors:
   - Functionality:

3. **Post-Migration State**
   - File locations:
   - Configuration:
   - Database:

#### Helm Deployment

- Chart version:
- Image tested:
- Issues found:
- Recommendations:

#### Kubernetes Deployment

- Manifest version:
- Image tested:
- Issues found:
- Recommendations:

### Action Items

1. [ ] Code changes needed
2. [ ] Documentation updates
3. [ ] Migration guide refinements
4. [ ] Helm chart adjustments
5. [ ] K8s manifest adjustments

### Test Environment

- Ubuntu version:
- Docker version:
- Helm version:
- kubectl version:
- MMRelay versions tested:

---

## Notes for Test Agent

1. **Preserve data**: Always backup before migrations
2. **Document everything**: Screenshots, logs, file listings
3. **Test real message flow**: Don't just check if services start
4. **Note warnings**: Even if app works, warnings matter for user experience
5. **Check permissions**: File ownership issues are common in Docker
6. **Verify both directions**: Matrix→Meshtastic AND Meshtastic→Matrix
7. **Test plugins**: If config includes plugins, verify they load

## Success Criteria

| Test           | Success Criteria                              |
| -------------- | --------------------------------------------- |
| Docker upgrade | App runs, messages flow, migration available  |
| Migration      | Data preserved, no manual intervention needed |
| Helm           | Clean install works, probes healthy           |
| K8s            | Clean install works, probes healthy           |
