# Release Checklist for 1.3

This checklist is required before tagging a 1.3 release.

## Pre-Tag Verification

- [ ] Version numbers match the release tag (code, docs, and packaging).
- [ ] `docs/MIGRATION_1.3.md` is accurate and complete.
- [ ] `docs/HELM.md` and `docs/KUBERNETES.md` match the 1.3 behavior.
- [ ] No legacy paths or legacy environment variables appear outside allowed legacy examples.

## Migration Checks

- [ ] Run `mmrelay migrate --dry-run` on a legacy fixture and review output.
- [ ] Run `mmrelay migrate --move` on a legacy fixture and confirm data lands under MMRELAY_HOME.
- [ ] Run `mmrelay verify-migration` and confirm exit code is 0.
- [ ] Run `mmrelay doctor --migration` and confirm there are no warnings.
- [ ] Credentials are present at MMRELAY_HOME and reported by verification.

## Helm Chart Checks

- [ ] `scripts/ci/helm_render_validate.sh` passes.
- [ ] `scripts/ci/check_container_paths.sh` passes.
- [ ] Helm render with persistence disabled shows `emptyDir` for `/data`.
- [ ] Helm render with `networkPolicy.enabled=true` produces a valid NetworkPolicy.

## Docker Image Checks

- [ ] Build or pull the 1.3 image tag and start a container with `/data` mounted.
- [ ] `mmrelay doctor --config /app/config.yaml` succeeds.
- [ ] `mmrelay verify-migration` returns exit code 0.

## CI Checks

- [ ] All required GitHub Actions workflows are green on `v13rc1-2`.
- [ ] No warnings or failures in lint, test, or packaging workflows on `v13rc1-2`.
