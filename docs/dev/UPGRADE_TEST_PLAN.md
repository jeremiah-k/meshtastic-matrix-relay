# Meshtastic Matrix Relay - Upgrade Test Plan

## Purpose

This document provides a comprehensive test plan for validating the upgrade path from Meshtastic Matrix Relay v1.2.11 to v1.3.x across all supported deployment methods (pipx/systemd, Docker Compose, and Kubernetes).

## Prerequisites

### Software Requirements

- Docker Engine (24.0.0+)
- Docker Compose v2.20.0+
- Python 3.10+ with pipx installed
- git
- Access to Docker Hub for pulling images
- For Kubernetes testing: microk8s or equivalent cluster

### Test Data

- A working v1.2.11 configuration file saved as `~/config.yaml.orig` (MASTER COPY - DO NOT MODIFY)
- Test Meshtastic device or simulator
- Test Matrix server/credentials

---

## Section 0: Environment Setup and Clean Slate

**CRITICAL: Always verify clean state before starting tests**

### 0.1 Check for Previous Test Data

```bash
# Check for existing installations
which mmrelay
systemctl --user status mmrelay 2>/dev/null
docker ps -a | grep mmrelay
docker images | grep mmrelay

# Check for leftover data
ls -la ~/.mmrelay/
ls -la ~/config.yaml*

# Check for running containers
docker ps -a | grep -E "mmrelay|watchtower"
```

### 0.2 Clean Slate Procedure

**If ANY of the above show previous test artifacts:**

```bash
# Stop and remove systemd service if exists
systemctl --user stop mmrelay 2>/dev/null
systemctl --user disable mmrelay 2>/dev/null
rm -f ~/.config/systemd/user/mmrelay.service
systemctl --user daemon-reload

# Remove pipx installation
pipx uninstall meshtastic-matrix-relay || true

# Stop and remove Docker containers
docker stop mmrelay watchtower 2>/dev/null || true
docker rm mmrelay watchtower 2>/dev/null || true

# Remove Docker images (optional, saves bandwidth if skipped)
docker rmi $(docker images | grep mmrelay | awk '{print $3}') 2>/dev/null || true

# Backup and remove old data (PRESERVE IF DEBUGGING)
if [ -d ~/.mmrelay ]; then
    mv ~/.mmrelay ~/.mmrelay.backup.$(date +%Y%m%d_%H%M%S)
fi
if [ -f ~/config.yaml ]; then
    mv ~/config.yaml ~/config.yaml.backup.$(date +%Y%m%d_%H%M%S)
fi

# Remove Kubernetes resources if exist
microk8s kubectl delete namespace mmrelay 2>/dev/null || true
```

### 0.3 Master Configuration Setup

```bash
# Verify master config exists
if [ ! -f ~/config.yaml.orig ]; then
    echo "ERROR: ~/config.yaml.orig not found!"
    echo "Create a working v1.2.11 config and save as ~/config.yaml.orig"
    exit 1
fi

# Verify master config is valid
head -20 ~/config.yaml.orig
```

**Expected structure for v1.2.11 config:**

```yaml
meshtastic:
  connection_type: serial # or network
  serial_port: /dev/ttyUSB0 # if serial
  # ... other meshtastic settings

matrix:
  homeserver: https://matrix.example.com
  bot_user_id: "@mmrelay:example.com"
  bot_access_token: "syt_..."
  # ... other matrix settings

# Relay configuration
# ...
```

---

## Section 1: pipx/systemd Service Test Flow

**Test the upgrade path for pipx installations with systemd service**

### 1.1 Install v1.2.11 with pipx

```bash
# Install v1.2.11
pipx install meshtastic-matrix-relay==1.2.11

# Verify installation
mmrelay --version  # Should show 1.2.11

# Create config directory and copy master config
mkdir -p ~/.mmrelay
cp ~/config.yaml.orig ~/config.yaml

# Initialize (if needed for v1.2.11)
mmrelay --config ~/config.yaml
```

### 1.2 Set Up and Test systemd Service (v1.2.11)

```bash
# Get the service file template from v1.2.11
curl -o /tmp/mmrelay.service https://raw.githubusercontent.com/geoffwhittington/meshtastic-matrix-relay/1.2.11/src/mmrelay/tools/mmrelay.service

# OR if testing from local repo, get from tag:
git show 1.2.11:src/mmrelay/tools/mmrelay.service > /tmp/mmrelay.service

# Edit the service file if needed (User= line should match your username)
sed "s/User=.*/User=$(whoami)/" /tmp/mmrelay.service > /tmp/mmrelay.service.edited

# Install service
mkdir -p ~/.config/systemd/user
cp /tmp/mmrelay.service.edited ~/.config/systemd/user/mmrelay.service

# Reload systemd
systemctl --user daemon-reload

# Start and enable service
systemctl --user enable mmrelay
systemctl --user start mmrelay

# Verify service is running
systemctl --user status mmrelay
journalctl --user -u mmrelay -n 50
```

**Expected Results:**

- Service starts successfully
- No errors in logs
- Relay connects to both Meshtastic and Matrix

### 1.3 Upgrade pipx Installation to v1.3.x

```bash
# Stop the service first
systemctl --user stop mmrelay

# Backup current config
cp ~/config.yaml ~/config.yaml.pre-upgrade

# Upgrade to latest version
pipx upgrade meshtastic-matrix-relay

# Verify new version
mmrelay --version  # Should show 1.3.x
```

### 1.4 Test Migration Commands (pipx)

```bash
# Dry run migration
mmrelay migrate --dry-run

# Expected output: Shows planned changes (config path, data directory, etc.)

# Run actual migration
mmrelay migrate

# Expected output: Migration successful

# Verify migration
mmrelay verify-migration

# Expected output: Verification passed
```

**Expected Migration Results:**

- Config moved to `~/.mmrelay/config.yaml` (if not already there)
- Data directory structure matches v1.3 expectations
- Old config backup created

### 1.5 Test systemd Service with v1.3

```bash
# The service file should still work, but may need updates
# Check if service file needs migration
cat ~/.config/systemd/user/mmrelay.service

# Start service with new version
systemctl --user start mmrelay

# Verify service is running
systemctl --user status mmrelay
journalctl --user -u mmrelay -n 50
```

**Expected Results:**

- Service starts successfully with v1.3
- Configuration loads correctly
- Relay functions normally

### 1.6 Stop systemd Service for Docker Tests

```bash
# Stop and disable service to prevent conflicts
systemctl --user stop mmrelay
systemctl --user disable mmrelay

# Verify stopped
systemctl --user status mmrelay
```

---

## Section 2: Docker Compose Upgrade Test

**Test the upgrade path using Docker Compose with proper volume mounting**

### 2.1 Prepare v1.2.11 Docker Environment

```bash
# Create test directory
mkdir -p ~/mmrelay-docker-test
cd ~/mmrelay-docker-test

# Create docker-compose.yaml for v1.2.11 (MATCHES ACTUAL 1.2.11 PATTERN)
cat > docker-compose.yaml << 'EOF'
services:
  mmrelay:
    image: ghcr.io/geoffwhittington/meshtastic-matrix-relay:1.2.11
    container_name: mmrelay
    restart: unless-stopped
    stop_grace_period: 30s
    user: "${UID:-1000}:${GID:-1000}"
    environment:
      - TZ=UTC
    volumes:
      # v1.2.11 used DUAL MOUNT PATTERN:
      # 1. Read-only config mount
      - ${MMRELAY_HOME:-$HOME}/.mmrelay/config.yaml:/app/config.yaml:ro
      # 2. Data directory mount
      - ${MMRELAY_HOME:-$HOME}/.mmrelay:/app/data
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0  # Adjust if using different device
    labels:
      - "com.centurylinklabs.watchtower.enable=true"
EOF

# Create .env file
cat > .env << EOF
UID=$(id -u)
GID=$(id -g)
MMRELAY_HOME=${HOME}
EOF

# Create config directory and copy master config
mkdir -p ~/.mmrelay
cp ~/config.yaml.orig ~/.mmrelay/config.yaml

# Verify config is in place
ls -la ~/.mmrelay/
```

### 2.2 Start v1.2.11 Container

```bash
cd ~/mmrelay-docker-test

# Start container
docker compose up -d

# Wait for startup
sleep 10

# Check logs
docker logs mmrelay --tail 50
```

**Expected Results:**

- Container starts successfully
- Config loads from `/app/config.yaml` (read-only mount)
- Data writes to `/app/data`
- Relay connects to both Meshtastic and Matrix

### 2.3 Set Up Watchtower (nickfedor fork)

**Important: Use nickfedor/watchtower fork, NOT containrrr/watchtower**

```bash
cd ~/mmrelay-docker-test

# Add watchtower service to docker-compose.yaml
cat >> docker-compose.yaml << 'EOF'

  watchtower:
    image: nickfedor/watchtower:latest
    container_name: watchtower
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - WATCHTOWER_CLEANUP=true
      - WATCHTOWER_LABEL_ENABLE=true
      - WATCHTOWER_INCLUDE_RESTARTING=true
      - WATCHTOWER_POLL_INTERVAL=300  # Check every 5 minutes for testing
    labels:
      - "com.centurylinklabs.watchtower.enable=false"  # Don't update watchtower itself
EOF

# Restart to include watchtower
docker compose down
docker compose up -d

# Verify watchtower is running
docker logs watchtower --tail 20
```

### 2.4 Simulate Upgrade to v1.3.x with Watchtower

**Before auto-upgrade, update docker-compose.yaml for v1.3 pattern:**

```bash
cd ~/mmrelay-docker-test

# Stop containers
docker compose down

# Update docker-compose.yaml to v1.3 pattern (UNIFIED MOUNT)
cat > docker-compose.yaml << 'EOF'
services:
  mmrelay:
    image: ghcr.io/geoffwhittington/meshtastic-matrix-relay:latest
    container_name: mmrelay
    restart: unless-stopped
    stop_grace_period: 30s
    user: "${UID:-1000}:${GID:-1000}"
    environment:
      - TZ=UTC
    volumes:
      # v1.3 uses UNIFIED MOUNT PATTERN:
      - ${MMRELAY_HOST_HOME:-$HOME}/.mmrelay:/data
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0
    labels:
      - "com.centurylinklabs.watchtower.enable=true"

  watchtower:
    image: nickfedor/watchtower:latest
    container_name: watchtower
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      - WATCHTOWER_CLEANUP=true
      - WATCHTOWER_LABEL_ENABLE=true
      - WATCHTOWER_INCLUDE_RESTARTING=true
      - WATCHTOWER_POLL_INTERVAL=300
    labels:
      - "com.centurylinklabs.watchtower.enable=false"
EOF

# Update .env for v1.3
cat > .env << EOF
UID=$(id -u)
GID=$(id -g)
MMRELAY_HOST_HOME=${HOME}
EOF

# Ensure config.yaml is at the right location for v1.3
if [ ! -f ~/.mmrelay/config.yaml ]; then
    cp ~/config.yaml.orig ~/.mmrelay/config.yaml
fi

# Start with new pattern
docker compose up -d

# Monitor logs
docker logs mmrelay --tail 50
```

**Expected Results:**

- Container starts with unified `/data` mount
- Config loads from `/data/config.yaml`
- Data directory is `/data`
- Relay continues to function normally

### 2.5 Test Migration Commands Inside Docker Container

**Users should be able to run migration commands from within the container:**

```bash
# Enter the running container
docker exec -it mmrelay bash

# Inside container - check current paths
ls -la /data/
cat /data/config.yaml | head -20

# Run migration dry-run
mmrelay migrate --dry-run

# Run actual migration
mmrelay migrate

# Verify migration
mmrelay verify-migration

# Exit container
exit
```

**Expected Results:**

- Migration commands run successfully inside container
- Config path verified at `/data/config.yaml`
- Data directory structure verified at `/data`
- Verification passes

### 2.6 Verify Container Logs and Functionality

```bash
# Check logs for any errors
docker logs mmrelay --tail 100

# Verify container is healthy
docker ps -a | grep mmrelay

# Test restart
docker restart mmrelay
sleep 10
docker logs mmrelay --tail 50
```

### 2.7 Stop Docker Containers for Next Test

```bash
cd ~/mmrelay-docker-test
docker compose down

# Verify stopped
docker ps -a | grep -E "mmrelay|watchtower"
```

---

## Section 3: systemd Service File Migration

**After Docker tests, migrate the systemd service file to v1.3 pattern**

### 3.1 Get Updated Service File

```bash
# Get the latest service file from v1.3
curl -o /tmp/mmrelay-v13.service https://raw.githubusercontent.com/geoffwhittington/meshtastic-matrix-relay/main/src/mmrelay/tools/mmrelay.service

# OR from local repo:
cp src/mmrelay/tools/mmrelay.service /tmp/mmrelay-v13.service

# Review changes
diff ~/.config/systemd/user/mmrelay.service /tmp/mmrelay-v13.service || true
```

### 3.2 Update Service File

```bash
# Backup old service file
cp ~/.config/systemd/user/mmrelay.service ~/.config/systemd/user/mmrelay.service.v12.backup

# Install new service file (update User= line)
sed "s/User=.*/User=$(whoami)/" /tmp/mmrelay-v13.service > ~/.config/systemd/user/mmrelay.service

# Reload systemd
systemctl --user daemon-reload
```

### 3.3 Test Updated Service

```bash
# Start service with updated file
systemctl --user start mmrelay

# Verify service status
systemctl --user status mmrelay

# Check logs
journalctl --user -u mmrelay -n 100 -f
```

**Expected Results:**

- Service starts successfully with updated service file
- Paths and config load correctly
- Relay functions normally

### 3.4 Clean Up systemd Service for Helm Test

```bash
# Stop and disable service
systemctl --user stop mmrelay
systemctl --user disable mmrelay

# Verify stopped
systemctl --user status mmrelay
```

---

## Section 4: Kubernetes/Helm Deployment Test

**Test Kubernetes deployment using microk8s and Helm chart**

### 4.1 Prepare Kubernetes Environment

```bash
# Verify microk8s is running
microk8s status

# Enable required addons if not already enabled
microk8s enable dns storage

# Create namespace
microk8s kubectl create namespace mmrelay

# Verify namespace
microk8s kubectl get namespaces
```

### 4.2 Prepare Configuration Secret

```bash
# Create ConfigMap from master config
microk8s kubectl create configmap mmrelay-config \
  --from-file=config.yaml=~/config.yaml.orig \
  --namespace=mmrelay

# Verify ConfigMap
microk8s kubectl get configmap -n mmrelay
microk8s kubectl describe configmap mmrelay-config -n mmrelay
```

### 4.3 Deploy with Helm (v1.3.x only - no legacy test)

**Note: Helm chart is v1.3+ only, no upgrade path from v1.2.11 needed**

```bash
# Add Helm repository (if published)
# helm repo add mmrelay https://geoffwhittington.github.io/meshtastic-matrix-relay
# helm repo update

# OR use local chart
cd ~/dev/meshtastic-matrix-relay  # or wherever you have the repo

# Create values override file
cat > /tmp/mmrelay-values.yaml << 'EOF'
image:
  repository: ghcr.io/geoffwhittington/meshtastic-matrix-relay
  tag: latest
  pullPolicy: Always

persistence:
  enabled: true
  storageClass: "microk8s-hostpath"  # Adjust for your cluster
  size: 1Gi

config:
  existingConfigMap: mmrelay-config

# Serial device passthrough (adjust as needed)
serialDevice:
  enabled: true
  path: /dev/ttyUSB0

# Resources
resources:
  requests:
    memory: "256Mi"
    cpu: "100m"
  limits:
    memory: "512Mi"
    cpu: "500m"
EOF

# Install with Helm
microk8s helm install mmrelay ./helm/mmrelay \
  --namespace mmrelay \
  --values /tmp/mmrelay-values.yaml

# Wait for deployment
microk8s kubectl rollout status deployment/mmrelay -n mmrelay

# Verify pod is running
microk8s kubectl get pods -n mmrelay
microk8s kubectl describe pod -n mmrelay -l app=mmrelay
```

### 4.4 Verify Helm Deployment

```bash
# Check pod logs
microk8s kubectl logs -n mmrelay -l app=mmrelay --tail=100 -f

# Check persistent volume
microk8s kubectl get pvc -n mmrelay
microk8s kubectl get pv

# Verify config mounted correctly
microk8s kubectl exec -n mmrelay -it deployment/mmrelay -- cat /data/config.yaml | head -20

# Test migration commands in pod
microk8s kubectl exec -n mmrelay -it deployment/mmrelay -- mmrelay migrate --dry-run
microk8s kubectl exec -n mmrelay -it deployment/mmrelay -- mmrelay verify-migration
```

**Expected Results:**

- Pod starts and runs successfully
- Config loads from ConfigMap
- Persistent volume attached and writable
- Relay connects to Meshtastic and Matrix
- Migration verification passes

### 4.5 Test Helm Upgrade

```bash
# Modify values file to test upgrade
cat >> /tmp/mmrelay-values.yaml << 'EOF'

# Add environment variable to test upgrade
env:
  - name: LOG_LEVEL
    value: DEBUG
EOF

# Perform Helm upgrade
microk8s helm upgrade mmrelay ./helm/mmrelay \
  --namespace mmrelay \
  --values /tmp/mmrelay-values.yaml

# Wait for rollout
microk8s kubectl rollout status deployment/mmrelay -n mmrelay

# Verify upgrade
microk8s kubectl logs -n mmrelay -l app=mmrelay --tail=50
```

### 4.6 Clean Up Kubernetes Resources

```bash
# Uninstall Helm release
microk8s helm uninstall mmrelay --namespace mmrelay

# Delete namespace (includes PVCs)
microk8s kubectl delete namespace mmrelay

# Verify cleanup
microk8s kubectl get all -n mmrelay 2>/dev/null || echo "Namespace deleted successfully"
```

---

## Section 5: Final Verification and Cleanup

### 5.1 Comprehensive Check

```bash
# Check all installation methods are cleaned up
which mmrelay  # Should still exist if pipx not uninstalled
pipx list | grep meshtastic  # Should show current version

systemctl --user status mmrelay  # Should be disabled/stopped

docker ps -a | grep mmrelay  # Should be empty

microk8s kubectl get namespace mmrelay  # Should not exist

# Verify master config unchanged
diff ~/config.yaml.orig ~/config.yaml.orig || echo "Master config unchanged"
```

### 5.2 Restore Environment (Optional)

```bash
# If you want to restore to clean state:
# systemctl --user stop mmrelay
# systemctl --user disable mmrelay
# pipx uninstall meshtastic-matrix-relay
# docker compose -f ~/mmrelay-docker-test/docker-compose.yaml down
# rm -rf ~/.mmrelay
# microk8s kubectl delete namespace mmrelay

echo "Testing complete!"
```

---

## Test Results Documentation Template

For each test run, document:

```markdown
## Test Run: YYYY-MM-DD

### Environment

- OS:
- Docker version:
- Python version:
- microk8s version (if applicable):

### Section 1: pipx/systemd

- [ ] v1.2.11 installation successful
- [ ] systemd service v1.2.11 working
- [ ] Upgrade to v1.3.x successful
- [ ] Migration commands successful
- [ ] systemd service v1.3 working

### Section 2: Docker Compose

- [ ] v1.2.11 container working
- [ ] Watchtower (nickfedor) setup successful
- [ ] Upgrade to v1.3.x successful
- [ ] Migration in container successful
- [ ] v1.3 container working

### Section 3: systemd Migration

- [ ] Service file updated
- [ ] Service working with new file

### Section 4: Kubernetes/Helm

- [ ] Helm deployment successful
- [ ] Pod running correctly
- [ ] Config loaded from ConfigMap
- [ ] PVC working
- [ ] Migration verification passed
- [ ] Helm upgrade successful

### Issues Found

- (List any issues encountered)

### Notes

- (Any additional observations)
```

---

## Common Issues and Troubleshooting

### Config File Not Found

- Verify `~/config.yaml.orig` exists and is valid
- Check file permissions (should be readable)

### Docker Permission Issues

- Ensure user is in docker group: `sudo usermod -aG docker $USER`
- Log out and back in for group changes to take effect

### Serial Device Access

- Add user to dialout group: `sudo usermod -aG dialout $USER`
- Verify device exists: `ls -la /dev/ttyUSB0`

### systemd Service Fails to Start

- Check logs: `journalctl --user -u mmrelay -n 100`
- Verify config file path in service file
- Check file permissions on config and data directories

### Kubernetes Pod CrashLoopBackOff

- Check pod logs: `microk8s kubectl logs -n mmrelay <pod-name>`
- Verify ConfigMap mounted correctly
- Check PVC is bound: `microk8s kubectl get pvc -n mmrelay`

### Migration Fails

- Check file permissions on `~/.mmrelay/`
- Verify config format is correct
- Run with verbose logging: `mmrelay migrate --verbose` (if supported)

---

## References

- [Migration Guide](../MIGRATION_1.3.md)
- [Docker Compose Sample](../../src/mmrelay/tools/sample-docker-compose-prebuilt.yaml)
- [systemd Service File](../../src/mmrelay/tools/mmrelay.service)
- [Helm Chart Documentation](../../helm/mmrelay/README.md)
- [Watchtower (nickfedor fork)](https://github.com/nickfedor/watchtower)
