#!/usr/bin/env bash
# Require a real, authorized peer configuration value through the Matrix path.
set -euo pipefail

export PYTHON_BIN="${PYTHON_BIN:?PYTHON_BIN must point at a python with mmrelay installed}"
export RA_ADMIN_REQUIRE_SUCCESS=true
export RA_ADMIN_REQUIRE_MESH=true
export MESH_CHANNEL_NAME=pki-test
export CI_ARTIFACT_DIR="${CI_ARTIFACT_DIR:-${PWD}/.ci-artifacts/remote-admin-success-integration}"
if [[ ${CI_ARTIFACT_DIR} != /* ]]; then
    CI_ARTIFACT_DIR="${PWD}/${CI_ARTIFACT_DIR}"
fi

bash "$(dirname "${BASH_SOURCE[0]}")/run-mmrelay-remote-admin-integration.sh"

# A bounded error is useful failure coverage, but does not satisfy this test.
"${PYTHON_BIN}" - "${CI_ARTIFACT_DIR}/shared/observability-summary.md" <<'PY'
import pathlib
import sys

summary = pathlib.Path(sys.argv[1]).read_text()
expected = "Remote --get returned peer lora.hop_limit=5 through the Matrix reply"
if expected not in summary:
    raise SystemExit("Positive integration failed: no verified peer value in the Matrix reply")
print(expected)
PY
