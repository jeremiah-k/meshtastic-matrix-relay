# Upgrade Test Execution Checklist (Repeatable)

Last updated: 2026-02-10 (America/Chicago)

Scope: repeatable validation of `1.2.11 -> 1.3.0` with focus on Docker dev image backwards compatibility first, then migration.

K8s/Helm scope for this cycle: fresh installs only (no migration/upgrade path validation).

## Preconditions

- `~/config.yaml.orig` exists and is valid.
- Docker can pull `ghcr.io/jeremiah-k/mmrelay:1.3.0-dev-v13rc1-2-f3ea4ea`.
- `pipx` available.

## Runbook (authoritative order)

### 1) Clean state

```bash
systemctl --user stop mmrelay 2>/dev/null || true
systemctl --user disable mmrelay 2>/dev/null || true
pkill -f mmrelay 2>/dev/null || true
pipx uninstall mmrelay 2>/dev/null || true
docker stop mmrelay-dev 2>/dev/null || true
docker rm mmrelay-dev 2>/dev/null || true
rm -rf ~/.mmrelay
rm -f ~/config.yaml ~/credentials.json
cp ~/config.yaml.orig ~/config.yaml
```

### 2) Seed legacy state with pipx 1.2.11

```bash
pipx install 'mmrelay[e2e]==1.2.11'
mmrelay --version
mmrelay auth login
```

Expected: credentials created by legacy auth flow.

Temporary workaround for realistic migration source:

```bash
mkdir -p ~/.mmrelay
if [ -f ~/credentials.json ]; then mv ~/credentials.json ~/.mmrelay/credentials.json; fi
```

### 3) Verify 1.2.11 runtime (manual)

```bash
mmrelay
```

Confirm in logs: startup, Matrix login/session restore, room sync, Meshtastic connect.
Stop process manually after verification.

### 4) Upgrade to 1.3.0 (pipx)

```bash
pipx uninstall mmrelay
cd ~/dev/mmrelay
git checkout f3ea4ea
pipx install -e '.[e2e]'
mmrelay --version
```

### 5) Critical test: Docker dev image backwards compatibility (NO migration yet)

```bash
docker run -d --name mmrelay-dev   --user "$(id -u):$(id -g)"   -e TZ=UTC -e PYTHONUNBUFFERED=1   -v "$HOME/config.yaml:/app/config.yaml:ro"   -v "$HOME/.mmrelay:/data"   ghcr.io/jeremiah-k/mmrelay:1.3.0-dev-v13rc1-2-f3ea4ea

docker logs mmrelay-dev --tail 100
```

Pass criteria:

- v1.3 starts without migration.
- Reads legacy credentials from `/data/credentials.json`.
- Connects to Matrix; room sync succeeds.
- Meshtastic connects.

Then stop container:

```bash
docker stop mmrelay-dev
```

### 6) Migration test (Docker)

Dry-run:

```bash
docker run --rm --user root   -v "$HOME/.mmrelay:/data"   -v "$HOME/config.yaml:/app/config.yaml:ro"   ghcr.io/jeremiah-k/mmrelay:1.3.0-dev-v13rc1-2-f3ea4ea   mmrelay migrate --dry-run
```

Apply:

```bash
docker run --rm --user root   -v "$HOME/.mmrelay:/data"   -v "$HOME/config.yaml:/app/config.yaml:ro"   ghcr.io/jeremiah-k/mmrelay:1.3.0-dev-v13rc1-2-f3ea4ea   mmrelay migrate
```

Verify:

```bash
docker run --rm --user root   -v "$HOME/.mmrelay:/data"   ghcr.io/jeremiah-k/mmrelay:1.3.0-dev-v13rc1-2-f3ea4ea   mmrelay verify-migration
```

Fix ownership after root-run migration:

```bash
sudo chown -R $(id -u):$(id -g) ~/.mmrelay
```

### 7) Post-migration runtime check (Docker)

```bash
docker run -d --name mmrelay-dev   --user "$(id -u):$(id -g)"   -e TZ=UTC -e PYTHONUNBUFFERED=1   -v "$HOME/config.yaml:/app/config.yaml:ro"   -v "$HOME/.mmrelay:/data"   ghcr.io/jeremiah-k/mmrelay:1.3.0-dev-v13rc1-2-f3ea4ea

docker logs mmrelay-dev --tail 100
```

Pass criteria:

- Credentials loaded from `/data/matrix/credentials.json`.
- Room sync and Meshtastic traffic continue.

## Current session result (2026-02-10)

- PASS: Docker dev image runs 1.3.0 against legacy data before migration.
- PASS: Migration dry-run/apply/verify commands work.
- PASS: Credentials moved to `~/.mmrelay/matrix/credentials.json`.
- PASS: `verify-migration` passes; legacy cleanup suggestion shown for `/data/store`.

## K8s/Helm fresh-install result (2026-02-10)

- PASS: Static Kubernetes manifests deploy successfully from clean namespace with:
  - `mmrelay-config` Secret
  - `mmrelay-matrix-auth` Secret
  - `kubectl apply -k deploy/k8s`
- PASS: Helm chart deploys successfully from clean namespace with:
  - `helm install mmrelay /home/agent/dev/mmrelay/deploy/helm/mmrelay -n mmrelay --values /tmp/mmrelay-helm-values.yaml`
- PASS: Pod reaches running state; `mmrelay doctor` reports HOME at `/data` and no migration required.

## Deployment smoothing updates applied

- Updated static k8s default image tag to `1.3.0` (avoid floating `latest`).
- Updated K8s/Helm docs to emphasize explicit tag pinning.
- Added rate-limit guardrail: avoid repeated namespace/PVC recreation with Matrix bootstrap auth secret unless necessary.

## Production code changes applied from findings

- `mmrelay auth login` now pre-creates required directories.
- Migration supports legacy `~/credentials.json` fallback only when file validates as Matrix credentials (`homeserver`, `access_token`, `user_id`).
- Tests added/updated for both behaviors.
