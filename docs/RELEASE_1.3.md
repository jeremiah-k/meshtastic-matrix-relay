# Release Checklist for 1.3

This checklist is required before tagging a 1.3 release.

## Pre-Tag Verification

- [ ] Version numbers match the release tag (code, docs, and packaging).
- [ ] `docs/MIGRATION_1.3.md` is accurate and complete.
- [x] `docs/HELM.md` and `docs/KUBERNETES.md` match the 1.3 behavior.
- [x] No legacy paths or legacy environment variables appear outside allowed legacy examples.

## Migration Checks

- [ ] Run `mmrelay migrate --dry-run` on a legacy fixture and review output.
- [ ] Run `mmrelay migrate --move` on a legacy fixture and confirm data lands under MMRELAY_HOME.
- [x] Run `mmrelay verify-migration` and confirm exit code is 0.
- [x] Run `mmrelay doctor --migration` and confirm there are no warnings.
- [x] Credentials are present at MMRELAY_HOME and reported by verification.

## Helm Chart Checks

- [ ] `scripts/ci/helm_render_validate.sh` passes.
- [x] `scripts/ci/check_container_paths.sh` passes.
- [x] Helm render with persistence disabled shows `emptyDir` for `/data`.
- [x] Helm render with `networkPolicy.enabled=true` produces a valid NetworkPolicy.

## Docker Image Checks

- [ ] Build or pull the 1.3 image tag and start a container with `/data` mounted.
- [x] `mmrelay doctor` succeeds.
- [x] `mmrelay verify-migration` returns exit code 0.

## CI Checks

- [ ] All required GitHub Actions workflows are green on `v13rc1-2`.
- [ ] No warnings or failures in lint, test, or packaging workflows on `v13rc1-2`.

## Notes From Final Pass (Test Environment)

- Verified in live test env (`coder`) on `v13rc1-2-dev`:
  - Helm deploy using local image `mmrelay:v13test` reached ready state and runtime logs showed E2EE status `ready`.
  - Docker Compose sample flow worked with live Matrix + Meshtastic config and produced healthy relay behavior.
  - E2EE store/key DB created under runtime home (`/data/matrix/store/...db` in container tests and `~/.mmrelay/matrix/store/...db` in local run).
- `scripts/ci/helm_render_validate.sh` is still unchecked in this checklist because the current run did not complete as a clean pass in this environment.
