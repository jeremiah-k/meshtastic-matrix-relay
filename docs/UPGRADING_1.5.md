# Upgrading MMRelay 1.4.1 to 1.5.0

This guide covers upgrading an existing relay **without losing its local
configuration, Matrix identity, message mappings or plugin state**. For
installations from 1.2.x or earlier, complete the [1.3 data migration](MIGRATION_1.3.md)
on a supported 1.4.1 installation **before** moving to 1.5.0.
Do not run two MMRelay processes against the same radio connection or data home.

## 1. Check the target before changing anything

1. Confirm the 1.5.0 tag, package or container image actually exists on your
   chosen distribution channel. Confirm you can retrieve the previous 1.4.1
   artifact if rollback is needed.
2. For a **native** install, provision **Python 3.12 or later** before upgrading.
   Python 3.11 cannot install MMRelay 1.5.0. Do not replace the pinned
   `mindroom-nio` provider with `matrix-nio`.
3. Check whether any Matrix room is encrypted. **Windows E2EE is unsupported**
   in 1.5.0; Windows users can continue with unencrypted Matrix rooms. For a
   native Linux/macOS encrypted-room install, retain the `mmrelay[e2e]` extra
   when upgrading and preserve the existing bot device and complete E2EE
   store. The base package alone does not include the optional E2EE dependencies.
4. If the same Matrix room is shared with another MMRelay instance or a
   MEDRE bridge, **every MMRelay instance must run 1.5.0 or later and
   trust every other relay**. Configure `matrix.trusted_relays` on each
   relay with the other bots' exact Matrix user IDs: A must list B, and
   B must list A. Verify each account before trusting it. Do not mix
   1.4.x relays into this setup.
   Relay claims from unlisted senders and invalid claims from trusted
   peers are **dropped** with warnings that name the sender; ordinary
   Matrix messages without relay claims remain unaffected. Avoid connecting
   multiple relays to the same radio mesh without a separately tested
   loop-prevention plan. See the
   [shared-room guidance](WHATS_NEW_1.5.md#sharing-rooms-with-other-relay-instances).
5. Record the installed version, effective data home and **active config file**.
   Run `mmrelay --version`, `mmrelay paths`, and `mmrelay config check` as the
   **same OS user, with the same environment, working directory, and global
   options** (`--home`, `--config`, if used) as the running service. An
   interactive shell can otherwise report a different home or config file.
   Inspect the actual service definition or launch command first. On container
   deployments, run these checks **inside the existing container** with its
   configured environment and mounts; do not infer host paths from `/data`.

## 2. Stop and back up the complete state

Stop the current MMRelay process/service before taking a filesystem-level
backup. SQLite and Matrix E2EE state are modified during operation; copying
live database/store files without coordination can produce an inconsistent
backup. A storage snapshot with application writes quiesced is also suitable.

Back up the **complete selected data home**, including:

| Path relative to data home | Purpose                                                      |
| -------------------------- | ------------------------------------------------------------ |
| `config.yaml` (if active)  | Room mapping, radio connection and trust configuration       |
| `matrix/credentials.json`  | Matrix bot credentials and device identity                   |
| `matrix/store/`            | Olm/Megolm keys, device and cross-signing state (if enabled) |
| `database/`                | SQLite messages, node mappings and other persistent state    |
| `plugins/`                 | Custom/community plugin configuration and local files        |

On native Linux/macOS the default home is `~/.mmrelay`; native Windows normally
uses `%LOCALAPPDATA%\mmrelay`; in Docker/Helm/Kubernetes it is `/data` in the
container, backed by the configured host mount or PVC. **These are defaults:**
`MMRELAY_HOME`, `--home`, installer migration and container mounts may change
them. Check `mmrelay paths` **with the service's settings** before stopping
it, and identify the effective config file from `--config` or the actual
config search path (which can include the service's working directory). If the
active config is outside the selected data home, **back it up separately** and
record the original absolute path and permissions. Include custom service
units, Compose files, environment files, and deployment overlays in your
upgrade backup. Protect backups like credentials—never publish them in issue
reports or CI.

For a native Linux/macOS instance using the **default** `~/.mmrelay` home,
after stopping it:

```bash
umask 077
BACKUP_DIR="$HOME/mmrelay-backups" # Must be outside ~/.mmrelay
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
tar -C "$HOME" -czf "$BACKUP_DIR/mmrelay-1.4.1-data-$(date +%Y%m%d-%H%M%S).tar.gz" .mmrelay

# If your active config is outside ~/.mmrelay, copy it separately:
# cp "/absolute/path/to/active/config.yaml" "$BACKUP_DIR/config.yaml"
# chmod 600 "$BACKUP_DIR/config.yaml"
```

For a custom data home, archive **that actual directory** to a secure
location outside it; do not run the default-home command unchanged. Check
that the archive and any separately backed-up configuration can be restored
before upgrading.

For Docker Compose, back up the host directory mounted to `/data` with the
container stopped. For Helm/Kubernetes, export the **current** Helm values,
Secret/ConfigMap and PVC snapshot (or an equivalent consistent PVC backup).
Keep those backups out of the replacement Pod/container and test restoration.

## 3. Upgrade using your existing installation method

### Native installation (pipx or uv)

Keep the tool installer you already use and verify it selects Python 3.12+.
A `pipx upgrade` or `uv tool upgrade` alone may **not** change an existing
Python 3.11 virtual environment to 3.12; recreate the tool environment
against the supported interpreter when necessary:

```bash
# pipx installation
pipx upgrade mmrelay

# OR an existing uv tool installation
uv tool upgrade mmrelay

mmrelay --version
mmrelay config check
```

To force a known-good Python version or avoid picking a later package, use
that installer's documented Python/version-selection options; validate the
result with `mmrelay --version` before restarting the service. For encrypted
rooms on Linux/macOS, keep the E2EE dependencies installed and follow the
[E2EE guide](E2EE.md). Do not install both nio providers.

### Docker Compose

Confirm that the Compose image points to the verified **1.5.0** tag or digest
rather than floating `latest`. Keep the **same** `/data` host mount and
preserved `.env`/Compose settings. Then:

```bash
docker compose pull mmrelay
docker compose up -d mmrelay
docker compose logs --tail=100 mmrelay
```

These commands assume the Compose file already points at your selected image.
Do not replace an existing customized Compose file with a fresh sample just
to change its image. See the [Docker guide](DOCKER.md).

### Helm

Use the matching released chart checkout. Chart defaults still select 1.4.1
until the deployment metadata is promoted, so **explicitly select the image**.
Before upgrading, save the current Helm values securely; they may contain
credentials. Check and back up the PVC and the config Secret/ConfigMap.

```bash
umask 077
helm get values mmrelay -n mmrelay -o yaml > mmrelay-values-before-1.5.yaml
chmod 600 mmrelay-values-before-1.5.yaml

helm upgrade mmrelay ./deploy/helm/mmrelay \
  --namespace mmrelay \
  --reuse-values \
  --set image.tag=1.5.0 \
  --set image.digest=""
```

`--reuse-values` protects existing `persistence.existingClaim`, secret names
and non-default settings; explicitly clearing an **old** digest ensures the
new tag is used. To pin an immutable digest, pass the **verified new digest**
instead of the empty value, **while retaining a nonempty `image.tag`**: the
current template renders `repository:tag@digest`. If chart values change
shape, review a migration values file instead of relying on blind value reuse.
See the [Helm guide](HELM.md).

### Kubernetes (static manifests / Kustomize)

Back up your **existing** Deployment, Secret references and PVC. Do **not**
apply a fresh release checkout's unmodified `deploy/k8s` base to an existing
installation: its default PVC (`mmrelay-data`), Secret names and other settings
may differ from the resources currently in use. Bring the released 1.5.0
manifest changes into **your existing overlay or customized manifests** and
update its image to the verified 1.5.0 tag or digest. Preserve the existing
`persistentVolumeClaim.claimName`, Secret/ConfigMap names and keys, namespace,
service account, environment variables, mounts, and other site settings.

For a deployment managed by a Kustomize overlay:

```bash
# Replace this with the path to your existing customized overlay.
OVERLAY=/path/to/your/mmrelay-overlay
kubectl get deployment mmrelay -n mmrelay -o yaml
kubectl kustomize "$OVERLAY"
kubectl diff -k "$OVERLAY" # Exit status 1 means differences were found.
# Apply only after checking the rendered PVC/Secret references and image.
kubectl apply -k "$OVERLAY"
kubectl rollout status deployment/mmrelay -n mmrelay
```

For a directly maintained manifest, perform the equivalent comparison before
`kubectl apply -f`. Never let a change of image inadvertently switch the
relay to a different (possibly empty) persistent volume. Adjust the
namespace/deployment name to match your actual installation.
See the [Kubernetes guide](KUBERNETES.md).

### Windows installer

Stop the relay; back up the data home reported by `mmrelay paths`; then install
the **published** 1.5.0 installer over the existing installation if its
upgrade path applies. Preserve the Matrix credentials, SQLite database, and
plugin files. The Windows installer supports **unencrypted Matrix rooms only**
for this release; see the [Windows guide](WINDOWS.md).

## 4. Validate before returning to normal operation

- Confirm `mmrelay --version` and `mmrelay config check` (or the equivalent
  commands inside the container) succeed without changing your configured home.
- Run `mmrelay doctor` and check Matrix authentication status and log output.
  On supported Linux/macOS E2EE deployments, verify that the **same device**
  can decrypt an existing encrypted room after restart.
- Check the correct channel-to-room mapping with **a small test message in
  each direction**, including replies/reactions where configured. Verify that
  messages do not appear in unrelated rooms or on another radio channel.
- Confirm plugins start once, reconnects do not create duplicate producers,
  and a failing radio send is recorded rather than silently treated as
  successful delivery. For shared rooms, verify that trusted bot MXIDs are
  configured exactly and review duplicate/echo behavior.
- Keep the old artifacts and backups until the new relay has completed a
  representative runtime period on your own topology.

## 5. Roll back if validation fails

Stop 1.5.0 first. Restore the **matching** 1.4.1 package or image **and** its
saved data-home snapshot, Helm values/Secrets or PVC as applicable. Do not
assume that downgrading the executable alone can read every newer SQLite or
E2EE store layout. Never reset cross-signing or remove a device merely as an
upgrade workaround. If restore fails, keep the backups intact and investigate
before restarting either version.

For a reported issue, include the MMRelay version, Python/platform, install
method, redacted config, and logs. **Do not** include credentials, private
Matrix stores, source-native packets or device keys.
