#!/usr/bin/env bash
#
# Containerized Helm validation script
#
# Uses a Docker image to run Helm without requiring a local Helm installation.
# Validates the Helm chart by linting, rendering templates, and validating with
# kubeconform, kube-linter, or a Python YAML parser fallback (offline).
#
# Requirements: Docker/Podman; optional validators (kubeconform or kube-linter)
# If Docker/Podman is not available, Helm rendering is skipped and pre-rendered
# YAML samples are validated instead.
#

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

HELM_IMAGE="${HELM_IMAGE:-alpine/helm:3.14.2}"
CHART_PATH="${CHART_PATH:-deploy/helm/mmrelay}"
RENDER_DIR="${RENDER_DIR:-/tmp/helm-render-$$}"
KUBECONFORM_BIN="${KUBECONFORM_BIN-}"
KUBE_LINTER_BIN="${KUBE_LINTER_BIN-}"
KUBE_LINTER_CONFIG="${KUBE_LINTER_CONFIG:-scripts/ci/kube-linter-config.yaml}"
KUBERNETES_VERSION="${KUBERNETES_VERSION:-1.29.0}"

if command -v podman &>/dev/null; then
	CONTAINER_CMD="podman"
elif command -v docker &>/dev/null; then
	CONTAINER_CMD="docker"
else
	CONTAINER_CMD=""
fi

if [[ -n ${CONTAINER_CMD} ]]; then
	echo -e "${GREEN}Using ${CONTAINER_CMD} and Helm image: ${HELM_IMAGE}${NC}"
	echo -e "${GREEN}Render directory: ${RENDER_DIR}${NC}"
else
	echo -e "${YELLOW}Helm container not available, skipping Helm render; performing YAML validation only${NC}"
fi

# cleanup removes the temporary Helm render directory referenced by RENDER_DIR if it exists.
cleanup() {
	if [[ -d ${RENDER_DIR} ]]; then
		rm -rf "${RENDER_DIR}"
	fi
}
trap cleanup EXIT

mkdir -p "${RENDER_DIR}"

# helm_in_container runs the configured Helm container image, mounting the current working directory at /workdir and forwarding all arguments to the containerized Helm command.
# helm_in_container runs Helm inside the configured container image using the detected container runtime and forwards all provided Helm arguments.
helm_in_container() {
	if [[ -z ${CONTAINER_CMD} ]]; then
		echo -e "${RED}ERROR: Helm container unavailable${NC}"
		return 1
	fi
	local workdir
	workdir="$(pwd)"
	${CONTAINER_CMD} run --rm \
		-v "${workdir}:/workdir" \
		-w /workdir \
		"${HELM_IMAGE}" \
		"$@"
}

# detect_validator locates kubeconform or kube-linter (including $HOME/bin/kube-linter-linux), sets KUBECONFORM_BIN or KUBE_LINTER_BIN accordingly, and prints which validator will be used or that it will fall back to YAML parse only.
detect_validator() {
	if [[ -z ${KUBECONFORM_BIN} ]] && command -v kubeconform &>/dev/null; then
		KUBECONFORM_BIN="$(command -v kubeconform)"
	fi

	if [[ -z ${KUBE_LINTER_BIN} ]]; then
		if command -v kube-linter &>/dev/null; then
			KUBE_LINTER_BIN="$(command -v kube-linter)"
		elif [[ -x "${HOME}/bin/kube-linter-linux" ]]; then
			KUBE_LINTER_BIN="${HOME}/bin/kube-linter-linux"
		fi
	fi

	if [[ -n ${KUBECONFORM_BIN} ]]; then
		echo -e "${GREEN}Using kubeconform validator: ${KUBECONFORM_BIN}${NC}"
		return 0
	fi

	if [[ -n ${KUBE_LINTER_BIN} ]]; then
		echo -e "${GREEN}Using kube-linter validator: ${KUBE_LINTER_BIN}${NC}"
		return 0
	fi

	echo -e "${YELLOW}No kubeconform or kube-linter found; falling back to YAML parse only${NC}"
	return 0
}

# validate_manifest validates a rendered Kubernetes manifest file by running kubeconform if available, falling back to kube-linter, and finally to a PyYAML parse that ensures each document includes `apiVersion` and `kind`; it returns a non-zero status on validation failure.
validate_manifest() {
	local output_file="$1"

	if [[ -n ${KUBECONFORM_BIN} ]]; then
		"${KUBECONFORM_BIN}" \
			-summary \
			-ignore-missing-schemas \
			-kubernetes-version "${KUBERNETES_VERSION}" \
			"${output_file}"
		return $?
	fi

	if [[ -n ${KUBE_LINTER_BIN} ]]; then
		"${KUBE_LINTER_BIN}" lint --config "${KUBE_LINTER_CONFIG}" "${output_file}"
		return $?
	fi

	python - "${output_file}" <<'PY'
import sys
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError as exc:
    raise SystemExit(
        "PyYAML is required for fallback validation. "
        "Install PyYAML or ensure kubeconform/kube-linter is available."
    ) from exc

path = Path(sys.argv[1])
docs = list(yaml.safe_load_all(path.read_text()))
for idx, doc in enumerate(docs, start=1):
    if doc is None:
        continue
    if "apiVersion" not in doc or "kind" not in doc:
        raise SystemExit(f"Document {idx} missing apiVersion or kind")
print("YAML parse ok")
PY
	return $?
}

# validate_pre_rendered_samples searches SAMPLE_MANIFEST_DIR (default deploy/k8s) for up to two levels of `.yaml` files and validates each with `validate_manifest`, printing status and exiting non‑zero on the first validation failure.
validate_pre_rendered_samples() {
	local sample_dir="${SAMPLE_MANIFEST_DIR:-deploy/k8s}"
	local files=()

	if [[ -d ${sample_dir} ]]; then
		local find_output
		find_output="$(mktemp)"
		trap 'rm -f "${find_output}"' RETURN
		if ! find "${sample_dir}" -maxdepth 2 -name "*.yaml" -print0 >"${find_output}"; then
			return 1
		fi
		while IFS= read -r -d '' file; do
			files+=("${file}")
		done <"${find_output}"
	fi

	if [[ ${#files[@]} -eq 0 ]]; then
		echo -e "${YELLOW}No pre-rendered YAML samples found; skipping validation${NC}"
		return 0
	fi

	for file in "${files[@]}"; do
		echo -e "${YELLOW}Validating sample ${file}...${NC}"
		set +e
		validate_manifest "${file}" >/dev/null 2>&1
		local validate_status=$?
		set -e
		if [[ ${validate_status} -ne 0 ]]; then
			echo -e "${RED}✗ Failed to validate ${file}${NC}"
			cat "${file}"
			return 1
		fi
		echo -e "${GREEN}✓ Validated ${file}${NC}"
	done
	return 0
}

# render_and_validate renders the Helm chart variant named by the first argument into RENDER_DIR/<name>.yaml, validates the rendered manifest, prints error and rendered output on failure, and returns non-zero if rendering or validation fail.
render_and_validate() {
	local name="$1"
	shift
	local output_file="${RENDER_DIR}/${name}.yaml"

	echo -e "${YELLOW}Rendering ${name}...${NC}"
	set +e
	helm_in_container template mmrelay "${CHART_PATH}" \
		--namespace mmrelay \
		"$@" >"${output_file}" 2>&1
	local render_status=$?
	set -e
	if [[ ${render_status} -ne 0 ]]; then
		echo -e "${RED}✗ Failed to render ${name}${NC}"
		echo "Error output:"
		cat "${output_file}"
		return 1
	fi

	echo -e "${GREEN}✓ Rendered ${name}${NC}"

	echo -e "${YELLOW}Validating ${name}...${NC}"
	local val_log="${RENDER_DIR}/${name}-validate.log"
	set +e
	validate_manifest "${output_file}" >"${val_log}" 2>&1
	local validate_status=$?
	set -e
	if [[ ${validate_status} -ne 0 ]]; then
		echo -e "${RED}✗ Failed to validate ${name}${NC}"
		echo "Validator output:"
		cat "${val_log}"
		echo "Rendered output:"
		cat "${output_file}"
		return 1
	fi
	echo -e "${GREEN}✓ Validated ${name}${NC}"
}

# test_expected_failure tests that rendering the mmrelay Helm chart with the given scenario name and optional Helm value overrides fails as expected; it saves Helm output to the render directory, returns success when rendering fails (and reports whether the failure message contains "empty", "required", or "missing"), and returns failure if rendering unexpectedly succeeds.
test_expected_failure() {
	local name="$1"
	shift

	echo -e "${YELLOW}Testing expected failure for ${name}...${NC}"
	local output_file="${RENDER_DIR}/${name}-expected-fail.yaml"

	set +e
	helm_in_container template mmrelay "${CHART_PATH}" \
		--namespace mmrelay \
		"$@" >"${output_file}" 2>&1
	local render_status=$?
	set -e
	if [[ ${render_status} -eq 0 ]]; then
		echo -e "${RED}✗ Expected failure for ${name} did not occur${NC}"
		echo "Error output:"
		cat "${output_file}"
		return 1
	else
		# Primary check: rendering failed as expected
		echo -e "${GREEN}✓ Expected failure confirmed for ${name}${NC}"
		# Secondary check: verify it's about empty data (informational)
		if grep -qi "empty\|required\|missing" "${output_file}"; then
			echo -e "${GREEN}  Failure message indicates validation error (expected)${NC}"
		else
			echo -e "${YELLOW}  Warning: Failure reason not recognized${NC}"
		fi
		return 0
	fi
}

detect_validator

if [[ -z ${CONTAINER_CMD} ]]; then
	validate_pre_rendered_samples
	exit $?
fi

echo "=================================="
echo "Step 1: Helm Lint"
echo "=================================="
set +e
helm_in_container lint "${CHART_PATH}" >/dev/null 2>&1
lint_status=$?
set -e
if [[ ${lint_status} -ne 0 ]]; then
	echo -e "${RED}✗ Helm lint failed${NC}"
	helm_in_container lint "${CHART_PATH}"
	exit 1
fi
echo -e "${GREEN}✓ Helm lint passed${NC}"

echo ""
echo "=================================="
echo "Step 2: Expected Failure Tests"
echo "=================================="

test_expected_failure \
	"empty-config-data" \
	--set config.enabled=true \
	--set config.source=secret \
	--set config.create=true \
	--set-string config.data=

test_expected_failure \
	"empty-credentials-data" \
	--set config.enabled=true \
	--set credentials.enabled=true \
	--set credentials.create=true \
	--set-string credentials.data=

echo ""
echo "=================================="
echo "Step 3: Render Variants"
echo "=================================="

render_and_validate \
	"secret-config" \
	--set config.enabled=true \
	--set config.source=secret \
	--set config.name=mmrelay-config \
	--set config.key=config.yaml \
	--set persistence.enabled=true \
	--set credentials.enabled=false

render_and_validate \
	"configmap-config" \
	--set config.enabled=true \
	--set config.source=configmap \
	--set config.name=mmrelay-config \
	--set config.key=config.yaml \
	--set persistence.enabled=true \
	--set credentials.enabled=false

render_and_validate \
	"with-credentials" \
	--set config.enabled=true \
	--set config.source=secret \
	--set config.name=mmrelay-config \
	--set config.key=config.yaml \
	--set persistence.enabled=true \
	--set credentials.enabled=true \
	--set credentials.secretName=mmrelay-credentials \
	--set credentials.key=credentials.json

render_and_validate \
	"persistence-disabled-no-matrixauth" \
	--set config.enabled=true \
	--set config.source=secret \
	--set config.name=mmrelay-config \
	--set config.key=config.yaml \
	--set persistence.enabled=false \
	--set matrixAuth.enabled=false \
	--set credentials.enabled=false

render_and_validate \
	"persistence-disabled-matrixauth" \
	--set config.enabled=true \
	--set config.source=secret \
	--set config.name=mmrelay-config \
	--set config.key=config.yaml \
	--set persistence.enabled=false \
	--set matrixAuth.enabled=true \
	--set credentials.enabled=false

render_and_validate \
	"networkpolicy-enabled" \
	--set config.enabled=true \
	--set config.source=secret \
	--set config.name=mmrelay-config \
	--set config.key=config.yaml \
	--set persistence.enabled=true \
	--set credentials.enabled=false \
	--set networkPolicy.enabled=true

echo ""
echo "=================================="
echo "Validation Complete"
echo "=================================="
echo -e "${GREEN}All checks passed!${NC}"
